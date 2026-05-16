import contextlib
import shutil
import subprocess  # nosec B404
import sys
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from dateutil import parser as date_parser
from ffmpeg import FFmpeg, FFmpegError
from ffmpeg import Progress as FFmpegProgress
from rich.progress import (
    BarColumn,
    SpinnerColumn,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.progress import Progress as RichProgress
from static_ffmpeg import run as static_ffmpeg_run

from . import playback, playlist, prompts, utils
from .common import (
    APP_STATE,
    CONSOLE,
    ask_confirm,
    get_default_directory,
    get_default_video_format,
    get_ffmpeg_format,
    pause,
    print_error,
    print_info,
    print_success,
    print_text,
    print_warning,
    return_to_main_menu,
)

PROGRESS_PERCENT_TOTAL = 100.0
FFMPEG_PROTOCOL_WHITELIST = "file,http,https,tcp,tls,crypto"


@dataclass
class ProgressState:
    rich_progress: RichProgress
    task_id: TaskID
    open_ended: bool
    duration: float | None
    output_path: str


def get_static_ffmpeg_paths():
    if APP_STATE.cached_ffmpeg_path and APP_STATE.cached_ffprobe_path:
        return (APP_STATE.cached_ffmpeg_path, APP_STATE.cached_ffprobe_path)
    try:
        ffmpeg_path, ffprobe_path = static_ffmpeg_run.get_or_fetch_platform_executables_else_raise()
    except Exception as error:
        sys.exit(f"ffmpeg not found in PATH and static-ffmpeg fallback failed: {error}")
    APP_STATE.cached_ffmpeg_path = ffmpeg_path
    APP_STATE.cached_ffprobe_path = ffprobe_path
    return (ffmpeg_path, ffprobe_path)


def get_ffmpeg_path():
    if APP_STATE.cached_ffmpeg_path is not None:
        return APP_STATE.cached_ffmpeg_path
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        APP_STATE.cached_ffmpeg_path = ffmpeg_path
        return APP_STATE.cached_ffmpeg_path
    return get_static_ffmpeg_paths()[0]


def get_ffprobe_path():
    if APP_STATE.cached_ffprobe_path is not None:
        return APP_STATE.cached_ffprobe_path
    ffprobe_path = shutil.which("ffprobe")
    if ffprobe_path:
        APP_STATE.cached_ffprobe_path = ffprobe_path
        return APP_STATE.cached_ffprobe_path
    return get_static_ffmpeg_paths()[1]


def get_progress_duration(m3u8_source):
    if playlist.is_open_ended_m3u8(m3u8_source):
        return (True, None)
    return (False, playlist.get_m3u8_duration(m3u8_source))


def create_rich_progress(open_ended_progress):
    if open_ended_progress:
        return RichProgress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            TimeElapsedColumn(),
            TextColumn("{task.fields[size]}"),
            console=CONSOLE,
        )
    return RichProgress(
        TextColumn("{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TextColumn("{task.fields[time_info]}"),
        TextColumn("{task.fields[size]}"),
        console=CONSOLE,
    )


def get_output_size_label(output_path):
    file_path = Path(output_path)
    if file_path.exists():
        return utils.format_file_size(file_path.stat().st_size)
    return utils.format_file_size(0)


def update_download_progress(state, current_seconds):
    size_str = get_output_size_label(state.output_path)
    if state.duration:
        current_time = utils.seconds_to_time_str(current_seconds)
        total_time = utils.seconds_to_time_str(state.duration)
        state.rich_progress.update(
            state.task_id, time_info=f"{current_time}/{total_time} | {size_str}", size=""
        )
    elif state.open_ended:
        media_str = utils.seconds_to_time_str(current_seconds)
        state.rich_progress.update(state.task_id, size=f"elapsed {media_str} | {size_str}")
    else:
        state.rich_progress.update(state.task_id, time_info="", size=size_str)


def handle_progress_bar(ffmpeg, output_filename, m3u8_source, output_path):
    try:
        open_ended_progress, duration_override = get_progress_duration(m3u8_source)
        rich_progress = create_rich_progress(open_ended_progress)
        with rich_progress:
            task_id = rich_progress.add_task(
                utils.get_short_filename(output_filename),
                total=None if open_ended_progress else PROGRESS_PERCENT_TOTAL,
                time_info="",
                size=utils.format_file_size(0),
            )
            progress_state = ProgressState(
                rich_progress=rich_progress,
                task_id=task_id,
                open_ended=open_ended_progress,
                duration=duration_override,
                output_path=output_path,
            )

            @ffmpeg.on("progress")
            def on_progress(progress: FFmpegProgress):
                current_seconds = progress.time.total_seconds()
                if progress_state.duration:
                    completed = min(
                        PROGRESS_PERCENT_TOTAL,
                        current_seconds / progress_state.duration * PROGRESS_PERCENT_TOTAL,
                    )
                    progress_state.rich_progress.update(progress_state.task_id, completed=completed)
                elif progress_state.open_ended:
                    progress_state.rich_progress.advance(progress_state.task_id)
                update_download_progress(progress_state, current_seconds)

            ffmpeg.execute()
        return True
    except KeyboardInterrupt:
        print_info("Stopping ffmpeg...", before=1)
        with contextlib.suppress(Exception):
            ffmpeg.terminate()
        return True
    except (OSError, FFmpegError, subprocess.SubprocessError) as error:
        print_error(f"ffmpeg failed: {str(error).strip()}")
        raise RuntimeError from error


def handle_file_already_exists(output_path):
    if Path(output_path).exists() and not ask_confirm(
        f'File already exists: "{output_path}". Download it again?'
    ):
        print_info("Download skipped.", before=1, after=1)
        pause()
        return_to_main_menu()
    return True


def run_subprocess(command):
    subprocess.run(command, check=True)  # nosec B603


def build_ffmpeg_command(m3u8_source, output_path, input_options=None):
    ffmpeg = (
        FFmpeg(executable=get_ffmpeg_path())
        .option("hide_banner")
        .option("loglevel", "warning")
        .option("stats")
        .option("y")
    )
    if input_options:
        ffmpeg.input(m3u8_source, input_options)
    else:
        ffmpeg.input(m3u8_source)
    ffmpeg.output(output_path, {"c": "copy", "f": get_ffmpeg_format(get_default_video_format())})
    return ffmpeg


def command_arguments(command):
    return command.arguments if isinstance(command, FFmpeg) else command


def handle_retry_command(command):
    try:
        arguments = command_arguments(command)
        print_text("Retrying command: " + " ".join(arguments))
        run_subprocess(arguments)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def print_command(command):
    arguments = command_arguments(command)
    print_text("Command: " + " ".join(arguments), before=1, after=1)


def run_download_command(command, output_filename, m3u8_source, output_path):
    try:
        handle_progress_bar(command, output_filename, m3u8_source, output_path)
        return True
    except (OSError, RuntimeError, subprocess.SubprocessError):
        retry_success = handle_retry_command(command)
        return bool(retry_success and Path(output_path).exists())


def get_output_path(output_filename):
    return str(Path(get_default_directory()) / output_filename)


def download_m3u8_video_url(m3u8_link, output_filename):
    output_path = get_output_path(output_filename)
    handle_file_already_exists(output_path)
    command = build_ffmpeg_command(m3u8_link, output_path)
    print_command(command)
    return run_download_command(command, output_filename, m3u8_link, output_path)


def download_m3u8_video_file(m3u8_file_path, output_filename):
    output_path = get_output_path(output_filename)
    handle_file_already_exists(output_path)
    command = build_ffmpeg_command(
        m3u8_file_path,
        output_path,
        input_options={"protocol_whitelist": FFMPEG_PROTOCOL_WHITELIST, "ignore_unknown": None},
    )
    print_command(command)
    return run_download_command(command, output_filename, m3u8_file_path, output_path)


def build_vod_filename(streamer_name, vod_id, title=None, stream_date=None):
    filename_parts = [streamer_name]
    formatted_date = utils.format_date(stream_date) if stream_date else None
    if formatted_date:
        filename_parts.append(formatted_date)
    if title:
        filename_parts.append(utils.sanitize_filename(title))
    filename_parts.append(f"[{vod_id}]")
    return " - ".join(filename_parts) + get_default_video_format()


def get_filename_for_file_source(m3u8_source, title=None, stream_date=None):
    streamer_name, vod_id = utils.parse_vod_filename(m3u8_source)
    return build_vod_filename(streamer_name, vod_id, title=title, stream_date=stream_date)


def get_filename_for_url_source(m3u8_source, title=None, stream_date=None):
    try:
        streamer_name = utils.parse_streamer_from_m3u8_link(m3u8_source)
        vod_id = utils.parse_video_id_from_m3u8_link(m3u8_source)
    except ValueError:
        streamer_name = "video"
        vod_id = "vod"
    return build_vod_filename(streamer_name, vod_id, title=title, stream_date=stream_date)


def download_m3u8_source(m3u8_source, title=None, stream_date=None):
    is_local_file = Path(m3u8_source).is_file()
    if is_local_file:
        output_filename = get_filename_for_file_source(
            m3u8_source, title=title, stream_date=stream_date
        )
        success = download_m3u8_video_file(m3u8_source, output_filename)
        if success:
            Path(m3u8_source).unlink()
    else:
        output_filename = get_filename_for_url_source(
            m3u8_source, title=title, stream_date=stream_date
        )
        success = download_m3u8_video_url(m3u8_source, output_filename)
    if not success:
        print_error(f"Download failed: {output_filename}", before=1, after=1)
        return False
    print_success(f"Downloaded to {get_output_path(output_filename)}", before=1, after=1)
    return True


def handle_download_menu(link, title=None, stream_datetime=None):
    playback_command = playback.get_playback_command()
    while True:
        action = prompts.select_download_action(has_player=bool(playback_command))
        if action == "download":
            download_m3u8_source(link, title, stream_datetime)
            pause()
            return_to_main_menu()
        elif action == "play" and playback_command:
            playback.open_media(playback_command, link)
        elif action == "back":
            return_to_main_menu()
        else:
            print_warning("Choose an action.", before=1, after=1)


def get_stream_date_from_m3u8(m3u8_file):
    try:
        date = None
        total_seconds = 0
        for line in Path(m3u8_file).read_text(encoding="utf-8").splitlines():
            if line.startswith("#ID3-EQUIV-TDTG:"):
                date = line.split(":", 1)[1].strip()
            if line.startswith("#EXT-X-TWITCH-TOTAL-SECS:"):
                total_seconds = int(float(line.split(":")[-1].strip()))
        if date is not None:
            date = date_parser.parse(date)
            adjusted_date = date - timedelta(seconds=total_seconds)
            adjusted_date_str = adjusted_date.strftime("%Y-%m-%d")
            return adjusted_date_str
    except (OSError, ValueError):
        return None


def handle_file_download_menu(m3u8_file_path):
    stream_date = get_stream_date_from_m3u8(m3u8_file_path)
    playback_command = playback.get_playback_command()
    while True:
        action = prompts.select_download_action(has_player=bool(playback_command))
        if action == "download":
            streamer_name, video_id = utils.parse_vod_filename(m3u8_file_path)
            output_filename = build_vod_filename(streamer_name, video_id, stream_date=stream_date)
            success = download_m3u8_video_file(m3u8_file_path, output_filename)
            if not success:
                print_error(f"Download failed: {output_filename}", before=1, after=1)
                return None
            print_success(f"Downloaded to {get_output_path(output_filename)}", before=1, after=1)
            break
        elif action == "play" and playback_command:
            playback.open_media(playback_command, m3u8_file_path)
        elif action == "back":
            return_to_main_menu()
        else:
            print_warning("Choose an action.", before=1, after=1)
