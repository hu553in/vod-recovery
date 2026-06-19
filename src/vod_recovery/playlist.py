import asyncio
import contextlib
import hashlib
import subprocess  # nosec B404
from pathlib import Path
from urllib.parse import urlparse

import httpx
import m3u8
import msgspec
import questionary
from ffmpeg import FFmpeg, FFmpegError
from m3u8.model import SegmentList
from tenacity import (
    AsyncRetrying,
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_fixed,
)

from . import downloads, utils
from .common import (
    RESOLUTIONS,
    ask_select,
    get_config_value,
    get_data_path,
    get_default_directory,
    print_blank,
    print_error,
    print_info,
    print_progress,
    print_success,
    print_warning,
    return_to_main_menu,
)

HTTP_OK = 200
HTTP_SERVER_ERROR = 500
BLOCKED_STATUS_CODES = {403, 404, 410}
M3U8_SEARCH_START_OFFSET = -30
M3U8_SEARCH_END_OFFSET = 60
M3U8_SEARCH_CONNECTOR_LIMIT = 100
M3U8_SEARCH_RETRIES = 2
M3U8_SEARCH_TIMEOUT = httpx.Timeout(5, connect=2)
M3U8_SEARCH_TOTAL_TIMEOUT_SECONDS = 60
M3U8_URL_HASH_LENGTH = 20
FFPROBE_TIMEOUT_SECONDS = 15
DEFAULT_FPS = 30
RESOLUTION_HEIGHTS = (
    (2160, "2160p"),
    (1440, "1440p"),
    (1080, "1080p"),
    (720, "720p"),
    (480, "480p"),
    (360, "360p"),
)
SEGMENT_VALIDATION_BATCH_SIZE = 250
SEGMENT_CONNECTOR_LIMIT = 150
SEGMENT_VALIDATION_TIMEOUT_SECONDS = 60
SECONDS_PER_MINUTE = 60
INITIAL_SEGMENT_SEARCH_HIGH = 100
MAX_SEGMENT_SEARCH_HIGH = 50_000
HTTP_CONNECT_TIMEOUT_SECONDS = 5
HTTP_RETRY_WAIT_SECONDS = 1
HTTP_TIMEOUT = httpx.Timeout(20, connect=HTTP_CONNECT_TIMEOUT_SECONDS)
HTTP_LIMITS = httpx.Limits(max_connections=SEGMENT_CONNECTOR_LIMIT)
RETRYABLE_HTTP_ERRORS = (httpx.HTTPError, OSError)
FFMPEG_PROTOCOL_WHITELIST = "file,http,https,tcp,tls,crypto"


class ProbeStream(msgspec.Struct):
    width: int | None = None
    height: int | None = None
    r_frame_rate: str = "30/1"


class ProbeData(msgspec.Struct):
    streams: list[ProbeStream] = msgspec.field(default_factory=list)


def request_with_retry(method, url, *, retries=3, timeout=30, **kwargs):
    for attempt in Retrying(
        stop=stop_after_attempt(retries),
        wait=wait_fixed(HTTP_RETRY_WAIT_SECONDS),
        retry=retry_if_exception_type(RETRYABLE_HTTP_ERRORS),
        reraise=True,
    ):
        with attempt:
            response = httpx.request(method, url, timeout=timeout, follow_redirects=True, **kwargs)
            if response.status_code >= HTTP_SERVER_ERROR:
                response.raise_for_status()
            return response
    raise RuntimeError(f"Could not fetch {url}")


def load_m3u8_source(m3u8_source):
    if is_http_url(m3u8_source):
        response = request_with_retry("GET", m3u8_source, timeout=30)
        response.raise_for_status()
        return m3u8.loads(response.text, uri=m3u8_source)
    return m3u8.load(m3u8_source)


def load_m3u8_file_with_uri(file_path, uri):
    return m3u8.loads(Path(file_path).read_text(encoding="utf-8"), uri=uri)


def is_http_url(value):
    parsed_url = urlparse(value)
    return parsed_url.scheme in ("http", "https")


def absolutize_playlist(loaded_playlist):
    for segment in loaded_playlist.segments:
        if segment.init_section is not None and segment.init_section.uri:
            segment.init_section.uri = segment.init_section.absolute_uri
        segment.uri = segment.absolute_uri
    return loaded_playlist


def write_m3u8_to_file(m3u8_link, destination_path, max_retries=5):
    destination = Path(destination_path)
    try:
        response = request_with_retry("GET", m3u8_link, retries=max_retries, timeout=30)
        if response.status_code == HTTP_OK:
            destination.write_text(response.text, encoding="utf-8")
            return destination_path
        if response.status_code in BLOCKED_STATUS_CODES:
            try:
                vod_id = utils.parse_video_id_from_m3u8_link(m3u8_link)
            except (IndexError, ValueError):
                vod_id = "video"
            generated_path = Path(get_default_directory()) / f"vod_{vod_id}_generated.m3u8"
            if generated_path.exists():
                content = generated_path.read_text(encoding="utf-8")
                destination.write_text(content, encoding="utf-8")
                return destination_path
            base_url = m3u8_link.replace("index-dvr.m3u8", "")
            generated_m3u8 = generate_m3u8_from_segments(base_url)
            if generated_m3u8:
                absolute_m3u8 = make_m3u8_segments_absolute(generated_m3u8, base_url)
                destination.write_text(absolute_m3u8, encoding="utf-8")
                return destination_path
    except (OSError, httpx.HTTPError, ValueError) as error:
        raise RuntimeError(f"Could not write M3U8 after {max_retries} attempts.") from error
    raise RuntimeError(f"Could not write M3U8 after {max_retries} attempts.")


def is_video_muted(m3u8_link):
    try:
        response = request_with_retry("GET", m3u8_link, timeout=20)
        if response.status_code == HTTP_OK:
            return bool("unmuted" in response.text)
        if response.status_code in BLOCKED_STATUS_CODES:
            try:
                vod_id = utils.parse_video_id_from_m3u8_link(m3u8_link)
            except (IndexError, ValueError):
                return False
            generated_path = Path(get_default_directory()) / f"vod_{vod_id}_generated.m3u8"
            if generated_path.exists():
                return bool("unmuted" in generated_path.read_text(encoding="utf-8"))
            return False
    except (OSError, httpx.HTTPError):
        return False
    return False


async def fetch_status(session, url, retries=5, timeout=30):
    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(retries),
            wait=wait_fixed(HTTP_RETRY_WAIT_SECONDS),
            retry=retry_if_exception_type(RETRYABLE_HTTP_ERRORS),
            reraise=True,
        ):
            with attempt:
                method = "HEAD" if url.endswith((".ts", ".mp4")) else "GET"
                response = await session.request(method, url, timeout=timeout)
                if method == "HEAD" and response.status_code != HTTP_OK:
                    response = await session.get(url, timeout=timeout)
                if response.status_code >= HTTP_SERVER_ERROR:
                    response.raise_for_status()
                if response.status_code == HTTP_OK:
                    if url.endswith(".m3u8"):
                        if "#EXTM3U" in response.text:
                            return url
                    elif url.endswith((".ts", ".mp4")) or response.content:
                        return url
                return None
    except (httpx.HTTPError, TimeoutError, ConnectionResetError, OSError):
        return None
    return None


def build_candidate_m3u8_url(domain, streamer_name, video_id, epoch_timestamp, quality):
    url_hash = hashlib.sha1(
        f"{streamer_name}_{video_id}_{epoch_timestamp}".encode(), usedforsecurity=False
    ).hexdigest()[:M3U8_URL_HASH_LENGTH]
    return (
        f"{domain.strip()}{url_hash}_{streamer_name}_{video_id}_{epoch_timestamp}/"
        f"{quality}/index-dvr.m3u8"
    )


def iter_m3u8_search_offsets(start_offset, end_offset):
    if 0 not in range(start_offset, end_offset):
        yield from range(start_offset, end_offset)
        return
    yield 0
    max_distance = max(abs(start_offset), abs(end_offset - 1))
    for distance in range(1, max_distance + 1):
        before = -distance
        after = distance
        if start_offset <= before < end_offset:
            yield before
        if start_offset <= after < end_offset:
            yield after


def build_candidate_m3u8_urls(domains, qualities, streamer_name, video_id, start_timestamp):
    urls = []
    for seconds in iter_m3u8_search_offsets(M3U8_SEARCH_START_OFFSET, M3U8_SEARCH_END_OFFSET):
        epoch_timestamp = utils.calculate_epoch_timestamp(start_timestamp, seconds)
        if epoch_timestamp is None:
            continue
        for domain in domains:
            if not domain.strip():
                continue
            urls.extend(
                build_candidate_m3u8_url(
                    domain, streamer_name, video_id, int(epoch_timestamp), quality
                )
                for quality in qualities
            )
    return urls


def create_m3u8_search_task(session, url):
    return asyncio.create_task(
        fetch_status(session, url, retries=M3U8_SEARCH_RETRIES, timeout=M3U8_SEARCH_TIMEOUT)
    )


def start_m3u8_search_tasks(session, candidate_urls):
    pending_tasks = set()
    next_url_index = 0
    while next_url_index < min(M3U8_SEARCH_CONNECTOR_LIMIT, len(candidate_urls)):
        pending_tasks.add(create_m3u8_search_task(session, candidate_urls[next_url_index]))
        next_url_index += 1
    return (pending_tasks, next_url_index)


async def cancel_m3u8_search_tasks(pending_tasks):
    for pending_task in pending_tasks:
        pending_task.cancel()
    await asyncio.gather(*pending_tasks, return_exceptions=True)


async def wait_for_m3u8_search_tasks(pending_tasks, search_deadline):
    remaining_seconds = search_deadline - asyncio.get_running_loop().time()
    if remaining_seconds <= 0:
        return (set(), pending_tasks, True)
    done_tasks, pending_tasks = await asyncio.wait(
        pending_tasks, timeout=remaining_seconds, return_when=asyncio.FIRST_COMPLETED
    )
    return (done_tasks, pending_tasks, not done_tasks)


def print_m3u8_search_timeout(progress_printed):
    if progress_printed:
        print_blank()
    print_warning(f"M3U8 search timed out after {M3U8_SEARCH_TOTAL_TIMEOUT_SECONDS} seconds.")
    return False


async def find_vod_playlist_url(streamer_name, video_id, start_timestamp):
    domains = utils.read_text_file(get_data_path("domains.list"))
    qualities = ["chunked", "1080p60"]
    print_info("Searching M3U8 URL...", before=1)
    candidate_urls = build_candidate_m3u8_urls(
        domains, qualities, streamer_name, video_id, start_timestamp
    )
    if not candidate_urls:
        print_error("Could not build M3U8 search URLs.", before=1)
        return None
    successful_url = None
    progress_printed = False
    try:
        limits = httpx.Limits(max_connections=M3U8_SEARCH_CONNECTOR_LIMIT)
        checked_count = 0
        search_deadline = asyncio.get_running_loop().time() + M3U8_SEARCH_TOTAL_TIMEOUT_SECONDS
        async with httpx.AsyncClient(limits=limits, timeout=M3U8_SEARCH_TIMEOUT) as session:
            pending_tasks, next_url_index = start_m3u8_search_tasks(session, candidate_urls)
            while pending_tasks:
                done_tasks, pending_tasks, timed_out = await wait_for_m3u8_search_tasks(
                    pending_tasks, search_deadline
                )
                if timed_out:
                    progress_printed = print_m3u8_search_timeout(progress_printed)
                    await cancel_m3u8_search_tasks(pending_tasks)
                    break
                for task in done_tasks:
                    checked_count += 1
                    try:
                        url = await task
                    except (httpx.HTTPError, TimeoutError, ConnectionResetError, OSError):
                        url = None
                    print_progress(f"Searching {checked_count}/{len(candidate_urls)} URLs")
                    progress_printed = True
                    if url:
                        successful_url = url
                        print_blank()
                        print_success(f"Found M3U8 URL: {successful_url}", after=1)
                        await cancel_m3u8_search_tasks(pending_tasks)
                        break
                    if next_url_index < len(candidate_urls):
                        pending_tasks.add(
                            create_m3u8_search_task(session, candidate_urls[next_url_index])
                        )
                        next_url_index += 1
                if successful_url:
                    break
    except Exception as e:
        print_error(f"M3U8 search failed: {e!s}", before=1)
        return None
    if progress_printed and successful_url is None:
        print_blank()
    return successful_url


def get_chunked_actual_resolution(m3u8_url):
    try:
        ffprobe = FFmpeg(executable=downloads.get_ffprobe_path()).input(
            m3u8_url, v="quiet", print_format="json", show_streams=None, select_streams="v:0"
        )
        output = ffprobe.execute(timeout=FFPROBE_TIMEOUT_SECONDS)
        if not output:
            return None
        probe_data = msgspec.json.decode(output, type=ProbeData)
        if not probe_data.streams:
            return None
        video_stream = probe_data.streams[0]
        width = video_stream.width
        height = video_stream.height
        fps = parse_frame_rate(video_stream.r_frame_rate)
        if not width or not height:
            return None
        res_name = resolution_name_from_height(height)
        return f"{res_name}{fps}"
    except (
        subprocess.TimeoutExpired,
        FFmpegError,
        msgspec.DecodeError,
        msgspec.ValidationError,
        ValueError,
    ):
        return None


def parse_frame_rate(fps_str):
    try:
        if "/" in fps_str:
            numerator, denominator = fps_str.split("/")
            return round(float(numerator) / float(denominator))
        return round(float(fps_str))
    except (ValueError, ZeroDivisionError):
        return DEFAULT_FPS


def resolution_name_from_height(height):
    for minimum_height, resolution_name in RESOLUTION_HEIGHTS:
        if height >= minimum_height:
            return resolution_name
    return "160p"


def find_quality_in_url(m3u8_link):
    for resolution in RESOLUTIONS:
        if f"/{resolution}/" in m3u8_link:
            return resolution
    return "chunked"


async def check_available_resolution(client, m3u8_link, found_quality, resolution):
    url = m3u8_link.replace(f"/{found_quality}/", f"/{resolution}/")
    try:
        response = await client.get(url, timeout=20)
        if response.status_code == HTTP_OK:
            return resolution
        if response.status_code in BLOCKED_STATUS_CODES:
            segment_url = url.replace("index-dvr.m3u8", "0.ts")
            segment_response = await client.head(segment_url, timeout=10)
            if segment_response.status_code == HTTP_OK:
                return resolution
    except httpx.HTTPError:
        return None
    return None


def collect_valid_resolutions(m3u8_link, found_quality):
    async def collect():
        async with httpx.AsyncClient(limits=HTTP_LIMITS, timeout=HTTP_TIMEOUT) as client:
            tasks = [
                check_available_resolution(client, m3u8_link, found_quality, resolution)
                for resolution in RESOLUTIONS
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            return [result for result in results if isinstance(result, str)]

    return asyncio.run(collect())


async def get_chunked_resolution_info_async(m3u8_link, found_quality, valid_resolutions):
    if "chunked" not in valid_resolutions:
        return None
    chunked_url = m3u8_link.replace(f"/{found_quality}/", "/chunked/")
    return await asyncio.to_thread(get_chunked_actual_resolution, chunked_url)


def get_chunked_resolution_info(m3u8_link, found_quality, valid_resolutions):
    try:
        return asyncio.run(
            get_chunked_resolution_info_async(m3u8_link, found_quality, valid_resolutions)
        )
    except TimeoutError:
        return None


def select_supported_quality(m3u8_link):
    if m3u8_link is None:
        return None
    always_best_quality = get_config_value("always_best_quality")
    found_quality = find_quality_in_url(m3u8_link)
    if always_best_quality is True and found_quality == "chunked":
        return m3u8_link
    print_info("Checking available qualities...")
    valid_resolutions = collect_valid_resolutions(m3u8_link, found_quality)
    chunked_resolution_info = get_chunked_resolution_info(
        m3u8_link, found_quality, valid_resolutions
    )
    if not valid_resolutions:
        return None
    valid_resolutions.sort(key=RESOLUTIONS.index)
    if always_best_quality:
        best_resolution = valid_resolutions[0]
        if best_resolution == found_quality:
            return m3u8_link
        return m3u8_link.replace(f"/{found_quality}/", f"/{best_resolution}/")
    user_option = get_user_resolution_choice(
        m3u8_link, valid_resolutions, found_quality, chunked_resolution_info
    )
    return user_option


def get_user_resolution_choice(
    m3u8_link, valid_resolutions, found_quality, chunked_resolution_info=None
):

    def quality_title(resolution):
        if resolution == "chunked":
            return chunked_resolution_info or "Source (best quality)"
        return resolution

    quality = ask_select(
        "Select quality:",
        choices=[
            questionary.Choice(quality_title(resolution), resolution)
            for resolution in valid_resolutions
        ]
        + [questionary.Choice("Back", "back")],
        default=found_quality if found_quality in valid_resolutions else None,
    )
    if quality == "back":
        return_to_main_menu()
    return m3u8_link.replace(f"/{found_quality}/", f"/{quality}/")


def get_local_playlist_path(m3u8_link):
    try:
        return utils.get_vod_filepath(
            utils.parse_streamer_from_m3u8_link(m3u8_link),
            utils.parse_video_id_from_m3u8_link(m3u8_link),
        )
    except (IndexError, ValueError):
        return str(Path(get_default_directory()) / "video.m3u8")


def unmute_vod(m3u8_link):
    video_filepath = get_local_playlist_path(m3u8_link)
    write_m3u8_to_file(m3u8_link, video_filepath)
    is_muted = is_video_muted(m3u8_link)
    loaded_playlist = absolutize_playlist(load_m3u8_file_with_uri(video_filepath, m3u8_link))
    for segment in loaded_playlist.segments:
        if "-unmuted" in segment.uri:
            segment.uri = segment.uri.replace("-unmuted", "-muted")
    loaded_playlist.dump(video_filepath)
    if is_muted:
        print_info(f"Playlist written to {Path(video_filepath)}.", after=1)


def build_available_segments_playlist(m3u8_link):
    unmute_vod(m3u8_link)
    vod_file_path = get_local_playlist_path(m3u8_link)
    loaded_playlist = m3u8.load(vod_file_path)
    segment_urls = [segment.absolute_uri for segment in loaded_playlist.segments]
    if not segment_urls:
        print_warning("No playlist segments found. Continuing with the original playlist.")
        with contextlib.suppress(FileNotFoundError):
            Path(vod_file_path).unlink()
        return None
    print_info("Checking segments...")
    segments = asyncio.run(validate_playlist_segments(segment_urls))
    if not segments:
        if "/highlight" not in m3u8_link:
            print_warning("No available segments found. Continuing with the original playlist.")
        with contextlib.suppress(FileNotFoundError):
            Path(vod_file_path).unlink()
        return None
    playlist_segments = set(segments)
    original_count = len(loaded_playlist.segments)
    loaded_playlist.segments = SegmentList(
        [
            segment
            for segment in loaded_playlist.segments
            if segment.uri in playlist_segments or segment.absolute_uri in playlist_segments
        ]
    )
    loaded_playlist.dump(vod_file_path)
    disabled_segments = original_count - len(loaded_playlist.segments)
    if disabled_segments:
        print_warning(f"Disabled {disabled_segments} unavailable segments.")
    return vod_file_path


def is_playlist_playable(m3u8_source):
    try:
        FFmpeg(executable=downloads.get_ffprobe_path()).input(
            m3u8_source, protocol_whitelist=FFMPEG_PROTOCOL_WHITELIST
        ).execute(timeout=20)
        return True
    except subprocess.TimeoutExpired:
        print_warning("ffprobe timed out. Using the original M3U8.", after=1)
        return False
    except (FFmpegError, OSError) as e:
        print_warning(f"Could not verify playlist: {e}")
        return False


def get_vod_id_or_default(m3u8_link):
    try:
        return utils.parse_video_id_from_m3u8_link(m3u8_link)
    except (IndexError, ValueError):
        return "video"


def is_blocked_playlist(m3u8_link):
    try:
        response = request_with_retry("HEAD", m3u8_link, timeout=10)
        return response.status_code in BLOCKED_STATUS_CODES
    except httpx.HTTPError:
        return False


def get_generated_playlist_path(vod_id):
    return str(Path(get_default_directory()) / f"vod_{vod_id}_generated.m3u8")


def try_build_checked_playlist(m3u8_link):
    try:
        cleaned_playlist = build_available_segments_playlist(m3u8_link)
    except (OSError, httpx.HTTPError, RuntimeError) as error:
        print_warning(f"Segment check failed: {error}. Continuing with the original playlist...")
        return None
    if cleaned_playlist and is_playlist_playable(cleaned_playlist):
        return cleaned_playlist
    return None


def try_unmute_playlist(m3u8_link):
    if not is_video_muted(m3u8_link):
        return None
    print_warning("Playlist contains muted or unavailable segments.")
    unmute_vod(m3u8_link)
    m3u8_source = get_local_playlist_path(m3u8_link)
    if is_playlist_playable(m3u8_source):
        return m3u8_source
    return m3u8_link


def remove_stale_local_playlist(m3u8_link, vod_id):
    try:
        file_path = utils.get_vod_filepath(
            utils.parse_streamer_from_m3u8_link(m3u8_link),
            utils.parse_video_id_from_m3u8_link(m3u8_link),
        )
    except (IndexError, ValueError):
        file_path = str(Path(get_default_directory()) / f"vod_{vod_id}.m3u8")
    with contextlib.suppress(FileNotFoundError):
        Path(file_path).unlink()


def validate_segments_without_cleanup(m3u8_link):
    print_info("Checking segments...")
    try:
        playlist_segments = get_all_playlist_segments(m3u8_link)

        async def validate_with_timeout():
            return await asyncio.wait_for(
                validate_playlist_segments(playlist_segments),
                timeout=SEGMENT_VALIDATION_TIMEOUT_SECONDS,
            )

        asyncio.run(validate_with_timeout())
    except TimeoutError:
        print_warning("Segment validation timed out. Continuing without validation...")
    except (OSError, httpx.HTTPError, RuntimeError) as error:
        print_warning(f"Segment validation failed: {error}. Continuing without validation.")


def process_m3u8_configuration(m3u8_link, skip_check=False):
    vod_id = get_vod_id_or_default(m3u8_link)
    generated_path = get_generated_playlist_path(vod_id)
    if is_blocked_playlist(m3u8_link) and Path(generated_path).exists():
        return generated_path
    check_segments = get_config_value("check_segments") and (not skip_check)
    if check_segments:
        checked_playlist = try_build_checked_playlist(m3u8_link)
        if checked_playlist:
            return checked_playlist
    unmuted_playlist = try_unmute_playlist(m3u8_link)
    if unmuted_playlist:
        return unmuted_playlist
    remove_stale_local_playlist(m3u8_link, vod_id)
    if check_segments:
        validate_segments_without_cleanup(m3u8_link)
    return m3u8_link


def get_all_playlist_segments(m3u8_link):
    video_file_path = get_local_playlist_path(m3u8_link)
    write_m3u8_to_file(m3u8_link, video_file_path)
    loaded_playlist = absolutize_playlist(load_m3u8_file_with_uri(video_file_path, m3u8_link))
    loaded_playlist.dump(video_file_path)
    return [segment.uri for segment in loaded_playlist.segments]


async def validate_playlist_segments(segments):
    valid_segments = []
    all_segments = [url.strip() for url in segments]
    available_segment_count = 0
    batch_size = SEGMENT_VALIDATION_BATCH_SIZE
    try:
        async with httpx.AsyncClient(limits=HTTP_LIMITS, timeout=HTTP_TIMEOUT) as session:
            for i in range(0, len(all_segments), batch_size):
                batch = all_segments[i : i + batch_size]
                tasks = [
                    asyncio.create_task(fetch_status(session, url, retries=3, timeout=30))
                    for url in batch
                ]
                try:
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    for url in results:
                        if url and (not isinstance(url, Exception)):
                            available_segment_count += 1
                            valid_segments.append(url)
                    checked_count = min(i + batch_size, len(all_segments))
                    print_progress(f"Checking segments {checked_count}/{len(all_segments)}")
                except Exception as e:
                    print_warning(f"Batch check failed: {e!s}", before=1)
                    continue
                await asyncio.sleep(0.5)
    except Exception as e:
        print_warning(f"Segment validation failed: {e!s}", before=1)
    print_blank()
    if available_segment_count == len(all_segments):
        print_success("All segments are available.", after=1)
    elif available_segment_count == 0:
        print_warning("No segments are available.", after=1)
    else:
        print_info(
            f"{available_segment_count} of {len(all_segments)} segments are available.", after=1
        )
    return valid_segments


def get_m3u8_duration(m3u8_source):
    try:
        loaded_playlist = load_m3u8_source(m3u8_source)
        total_duration = sum(segment.duration for segment in loaded_playlist.segments)
        return total_duration if total_duration > 0 else None
    except Exception:
        return None


def is_open_ended_m3u8(m3u8_link):
    try:
        return not load_m3u8_source(m3u8_link).is_endlist
    except Exception:
        return True


def make_m3u8_segments_absolute(m3u8_content, base_url):
    loaded_playlist = m3u8.loads(m3u8_content, uri=base_url)
    return absolutize_playlist(loaded_playlist).dumps()


def generate_m3u8_from_segments(base_url, segment_duration=10.0):
    chunk_ext = ".ts"

    def check_segment(n, retries=2):
        try:
            url = f"{base_url}{n}{chunk_ext}"
            resp = request_with_retry("HEAD", url, retries=retries, timeout=10)
            return resp.status_code == HTTP_OK
        except httpx.HTTPError:
            return False

    if not check_segment(0):
        chunk_ext = ".mp4"
        if not check_segment(0):
            return None
    print_info(
        f"Segments are accessible ({chunk_ext}), "
        "but the playlist is blocked. Generating playlist..."
    )
    low, high = (0, INITIAL_SEGMENT_SEARCH_HIGH)
    while check_segment(high):
        low = high
        high *= 2
        if high > MAX_SEGMENT_SEARCH_HIGH:
            break
    while low < high:
        mid = (low + high + 1) // 2
        if check_segment(mid):
            low = mid
        else:
            high = mid - 1
    last_segment = low
    approximate_minutes = (last_segment + 1) * segment_duration / SECONDS_PER_MINUTE
    print_success(f"Found {last_segment + 1} segments (~{approximate_minutes:.0f} min)")
    generated_playlist = m3u8.M3U8()
    for key, value in {
        "version": 3,
        "target_duration": int(segment_duration),
        "playlist_type": "VOD",
        "media_sequence": 0,
        "is_endlist": True,
    }.items():
        setattr(generated_playlist, key, value)
    for i in range(last_segment + 1):
        generated_playlist.add_segment(
            m3u8.Segment(uri=f"{i}{chunk_ext}", duration=segment_duration)
        )
    return generated_playlist.dumps()
