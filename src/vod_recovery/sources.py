import contextlib
import re
import textwrap
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import httpx
import msgspec
import questionary
from dateutil import parser as date_parser
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

from . import downloads, playlist, recovery, utils
from .common import ReturnToMainError, ask_select, ask_text, print_error, print_info, print_warning

HTTP_OK = 200
RECENT_STREAM_DAYS = 60
ROWS_PER_PAGE = 50
SECONDS_PER_HOUR = 3600.0
STREAM_TITLE_MAX_LENGTH = 90
FETCH_RETRIES = 3


class VodVodDuration(msgspec.Struct):
    Valid: bool = False
    Float64: float = 0.0


class VodVodMetadata(msgspec.Struct):
    StartTime: str = ""
    HlsDurationSeconds: VodVodDuration | None = None
    TitleAtStart: str = ""
    StreamID: str = ""


class VodVodItem(msgspec.Struct):
    Metadata: VodVodMetadata | None = None


class TwitchVideoNode(msgspec.Struct):
    id: str = ""
    title: str = ""
    created_at: str | None = msgspec.field(default=None, name="createdAt")
    published_at: str | None = msgspec.field(default=None, name="publishedAt")
    length_seconds: float = msgspec.field(default=0.0, name="lengthSeconds")
    preview_thumbnail_url: str = msgspec.field(default="", name="previewThumbnailURL")
    animated_preview_url: str = msgspec.field(default="", name="animatedPreviewURL")


class TwitchVideoEdge(msgspec.Struct):
    node: TwitchVideoNode | None = None


class TwitchVideos(msgspec.Struct):
    edges: list[TwitchVideoEdge] = msgspec.field(default_factory=list)


class TwitchUser(msgspec.Struct):
    videos: TwitchVideos | None = None


class TwitchData(msgspec.Struct):
    user: TwitchUser | None = None


class TwitchGraphQLResponse(msgspec.Struct):
    data: TwitchData | None = None


@retry(
    stop=stop_after_attempt(FETCH_RETRIES),
    wait=wait_fixed(1),
    retry=retry_if_exception_type(httpx.HTTPError),
    reraise=True,
)
def fetch_vodvod_streams(streamer_name):
    try:
        response = httpx.get(
            f"https://api.vodvod.top/channels/@{streamer_name}",
            headers=utils.get_user_agent_headers(),
            timeout=15,
            follow_redirects=True,
        )
        if response.status_code != HTTP_OK:
            return None
        data = msgspec.json.decode(response.content, type=list[VodVodItem])
        if not data:
            return None
        streams = []
        for item in data:
            try:
                metadata = item.Metadata
                if metadata is None:
                    continue
                start_time = metadata.StartTime
                if not start_time:
                    continue
                dt_utc = date_parser.isoparse(start_time)
                dt_local = dt_utc.astimezone()
                duration_hours = None
                hls_duration = metadata.HlsDurationSeconds
                if hls_duration is not None and hls_duration.Valid:
                    duration_seconds = hls_duration.Float64
                    if duration_seconds > 0:
                        duration_hours = round(duration_seconds / 3600, 1)
                streams.append(
                    {
                        "dt_utc": dt_utc.strftime("%Y-%m-%d %H:%M:%S"),
                        "dt_local": dt_local.strftime("%Y-%m-%d %H:%M:%S"),
                        "title": metadata.TitleAtStart,
                        "duration": duration_hours,
                        "stream_id": metadata.StreamID,
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue
        return streams if streams else None
    except (msgspec.DecodeError, msgspec.ValidationError, ValueError, TypeError):
        return None


def merge_stream_sources(api_streams, vod_streams):
    api_streams = api_streams or []
    vod_streams = vod_streams or []
    if not api_streams and not vod_streams:
        return None
    merged = {}
    order = []
    fallback_counter = 0

    def make_key(stream):
        nonlocal fallback_counter
        stream_id = stream.get("stream_id") if isinstance(stream, dict) else None
        if stream_id:
            return f"id:{stream_id}"
        dt_utc = stream.get("dt_utc") if isinstance(stream, dict) else None
        if dt_utc:
            return f"utc:{dt_utc}"
        fallback_counter += 1
        return f"idx:{fallback_counter}"

    for stream in api_streams:
        key = make_key(stream)
        if key not in merged:
            merged[key] = stream
            order.append(key)
    for stream in vod_streams:
        key = make_key(stream)
        if key in merged:
            combined = merged[key].copy()
            combined.update(stream)
            merged[key] = combined
        else:
            merged[key] = stream
            order.append(key)
    result = [merged[key] for key in order]
    result.sort(key=lambda s: s.get("dt_utc") or "", reverse=True)
    return result if result else None


@retry(
    stop=stop_after_attempt(FETCH_RETRIES),
    wait=wait_fixed(1),
    retry=retry_if_exception_type(httpx.HTTPError),
    reraise=True,
)
def fetch_twitch_streams(streamer_name, max_streams=100):
    try:
        query = """
        query($login: String!, $first: Int!) {
            user(login: $login) {
                videos(first: $first) {
                    edges {
                        node {
                            id
                            title
                            createdAt
                            publishedAt
                            lengthSeconds
                            previewThumbnailURL
                            animatedPreviewURL
                        }
                    }
                }
            }
        }
        """
        payload = {"query": query, "variables": {"login": streamer_name, "first": max_streams}}
        res = httpx.post(
            "https://gql.twitch.tv/gql",
            json=payload,
            headers={
                "Client-ID": "ue6666qo983tsx6so1t0vnawi233wa",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0",
            },
            timeout=30,
            follow_redirects=True,
        )
        if res.status_code != HTTP_OK:
            return None
        data = msgspec.json.decode(res.content, type=TwitchGraphQLResponse)
        if data.data is None:
            return None
        user_data = data.data.user
        if user_data is None or user_data.videos is None:
            return None
        streams = []
        for edge in user_data.videos.edges:
            node = edge.node
            if node is None:
                continue
            try:
                created_at_iso = node.created_at or node.published_at
                if not created_at_iso:
                    continue
                dt_utc = date_parser.isoparse(created_at_iso)
                if dt_utc < datetime.now(dt_utc.tzinfo) - timedelta(days=RECENT_STREAM_DAYS):
                    continue
                stream_id, timestamp = extract_vod_id_from_preview(
                    streamer_name, node.preview_thumbnail_url, node.animated_preview_url
                )
                final_utc = dt_utc
                if timestamp:
                    with contextlib.suppress(TypeError, ValueError, OSError):
                        final_utc = datetime.fromtimestamp(int(timestamp), UTC)
                streams.append(
                    {
                        "dt_utc": final_utc.strftime("%Y-%m-%d %H:%M:%S"),
                        "dt_local": final_utc.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
                        "title": node.title,
                        "duration": node.length_seconds / SECONDS_PER_HOUR,
                        "stream_id": stream_id or node.id,
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue
        return streams
    except (msgspec.DecodeError, msgspec.ValidationError, ValueError, TypeError):
        return None


def extract_vod_id_from_preview(streamer_name, *urls):
    pattern = re.compile(rf"_{re.escape(streamer_name)}_(?P<video_id>\d+)_(?P<timestamp>\d+)")
    for preview_url in urls:
        if not preview_url:
            continue
        for part in preview_url.split("/"):
            match = pattern.search(part)
            if match:
                return (match.group("video_id"), match.group("timestamp"))
    return (None, None)


def recover_recent_streams(streamer_name=None):
    if not streamer_name:
        streamer_name = ask_text("Streamer name:").strip().lower()
    print_info("Searching recent streams...", before=1)
    streams = find_recent_streams(streamer_name)
    if not streams:
        print_warning("No recent streams found.", before=1)
        return
    streams = normalize_streams(streams)
    if not streams:
        print_warning("No recoverable streams found.", before=1)
        return
    print_info(f"Found {len(streams)} streams.")
    current_page = 1
    total_pages = (len(streams) + ROWS_PER_PAGE - 1) // ROWS_PER_PAGE
    while True:
        page_streams = get_page_streams(streams, current_page)
        choices = [
            questionary.Choice(format_stream_choice(stream), ("stream", stream))
            for stream in page_streams
        ]
        if current_page > 1:
            choices.append(questionary.Choice("Previous page", ("previous", None)))
        if current_page < total_pages:
            choices.append(questionary.Choice("Next page", ("next", None)))
        choices.append(questionary.Choice("Back", ("back", None)))
        action, stream = ask_select(
            f"Recent streams {current_page}/{total_pages}:", choices=choices
        )
        if action == "stream":
            recover_selected_stream(streamer_name, stream)
            return
        if action == "previous":
            current_page -= 1
        elif action == "next":
            current_page += 1
        elif action == "back":
            return


def find_recent_streams(streamer_name):
    def future_result(future):
        try:
            return future.result()
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        future_api = executor.submit(fetch_twitch_streams, streamer_name)
        future_vod = executor.submit(fetch_vodvod_streams, streamer_name)
        api_streams = future_result(future_api)
        vod_streams = future_result(future_vod)
    return merge_stream_sources(api_streams, vod_streams)


def normalize_streams(streams):
    normalized = []
    for row in streams:
        try:
            video_id = row["stream_id"]
            normalized_video_id = str(video_id).strip()
            if not normalized_video_id or normalized_video_id.lower() == "none":
                continue
            normalized.append(
                {
                    "video_id": normalized_video_id,
                    "date_local": row["dt_local"],
                    "date_utc": row["dt_utc"],
                    "title": row["title"],
                    "duration": row.get("duration"),
                }
            )
        except (KeyError, TypeError):
            continue
    return normalized


def get_page_streams(streams, page_num):
    start_idx = (page_num - 1) * ROWS_PER_PAGE
    end_idx = min(start_idx + ROWS_PER_PAGE, len(streams))
    return streams[start_idx:end_idx]


def format_stream_choice(stream):
    duration = stream.get("duration")
    duration_str = f"{round(duration, 1)} h" if duration is not None else "unknown duration"
    title = textwrap.shorten(stream["title"], width=STREAM_TITLE_MAX_LENGTH, placeholder="...")
    return f"{stream['date_local']} | {duration_str} | {title}"


def recover_selected_stream(streamer_name, stream):
    print_info(f"Recovering: {stream['date_local']} | {stream['title']}", before=1)
    video_id = stream["video_id"]
    timestamp = parse_stream_timestamp(stream["date_utc"])
    if not timestamp:
        return
    try:
        m3u8_source = recovery.recover_vod(streamer_name, video_id, timestamp)
    except ReturnToMainError:
        raise
    except Exception as e:
        print_error(f"Recovery failed for VOD {video_id}: {e}")
        return
    if m3u8_source:
        m3u8_source = playlist.process_m3u8_configuration(m3u8_source)
        downloads.handle_download_menu(
            m3u8_source, title=stream["title"], stream_datetime=timestamp
        )
    else:
        print_error(f"Could not recover VOD {video_id}.", before=1)


def parse_stream_timestamp(date_utc):
    timestamp = utils.format_iso_datetime(date_utc)
    if timestamp:
        return timestamp
    print_error(f"Could not parse stream time: {date_utc}")
    return None
