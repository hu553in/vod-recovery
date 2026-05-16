import re
import textwrap
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from dateutil import parser as date_parser
from fake_useragent import UserAgent
from pathvalidate import sanitize_filename as pathvalidate_sanitize_filename
from rich.filesize import decimal as format_decimal_filesize

from .common import APP_STATE, DEFAULT_USER_AGENT, get_default_directory, print_warning

SHORT_FILENAME_MAX_LENGTH = 30
SECONDS_PER_HOUR = 3600
SECONDS_PER_MINUTE = 60
MIN_SHORT_NAME_PARTS = 2
TWITCH_VOD_PATH_PATTERN = re.compile(
    r"^[^_]+_(?P<streamer_name>.+)_(?P<video_id>[^_]+)_(?P<timestamp>\d+)$"
)


def format_iso_datetime(iso_datetime: str):
    if not iso_datetime:
        return None
    try:
        dt = date_parser.parse(iso_datetime.strip())
        if dt.tzinfo is not None:
            dt = dt.astimezone(UTC).replace(tzinfo=None)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OverflowError):
        return None


def sanitize_filename(filename, restricted=False):
    platform = "windows" if restricted else "universal"
    sanitized = pathvalidate_sanitize_filename(
        str(filename), replacement_text="_", platform=platform
    )
    sanitized = re.sub("_+", "_", sanitized).strip(" _")
    return sanitized or "_"


def read_text_file(text_file_path):
    return Path(text_file_path).read_text(encoding="utf-8").splitlines()


def get_vod_filepath(streamer_name, video_id):
    return str(Path(get_default_directory()) / f"{streamer_name}_{video_id}.m3u8")


def get_user_agent_headers():
    try:
        if APP_STATE.cached_user_agent is None:
            APP_STATE.cached_user_agent = UserAgent(fallback=DEFAULT_USER_AGENT)
        user_agent = APP_STATE.cached_user_agent.random
    except Exception:
        user_agent = DEFAULT_USER_AGENT
    return {"user-agent": user_agent}


def calculate_epoch_timestamp(timestamp, seconds):
    try:
        epoch_timestamp = (
            date_parser.parse(timestamp) + timedelta(seconds=seconds) - datetime(1970, 1, 1)
        ).total_seconds()
        return epoch_timestamp
    except (TypeError, ValueError, OverflowError):
        return None


def calculate_days_since_broadcast(start_timestamp):
    if start_timestamp is None:
        return 0
    vod_age = datetime.now(UTC).replace(tzinfo=None) - date_parser.parse(start_timestamp)
    return max(vod_age.days, 0)


def parse_twitch_vod_path(m3u8_link):
    path = urlparse(str(m3u8_link)).path or str(m3u8_link)
    for segment in path.split("/"):
        match = TWITCH_VOD_PATH_PATTERN.match(segment)
        if match:
            return match.groupdict()
    raise ValueError(f"Could not parse Twitch VOD path: {m3u8_link}")


def parse_streamer_from_m3u8_link(m3u8_link):
    return parse_twitch_vod_path(m3u8_link)["streamer_name"]


def parse_video_id_from_m3u8_link(m3u8_link):
    return parse_twitch_vod_path(m3u8_link)["video_id"]


def parse_vod_filename(m3u8_video_filename):
    base = Path(m3u8_video_filename).name
    try:
        streamer_name, video_id = base.split(".m3u8", 1)[0].rsplit("_", 1)
        return (streamer_name, video_id)
    except ValueError:
        print_warning(f"Could not parse M3U8 filename: {base}")
        return ("video", "vod")


def get_short_filename(filename):
    base_name = Path(filename).stem
    if " - " in base_name:
        parts = base_name.split(" - ")
        if len(parts) >= MIN_SHORT_NAME_PARTS:
            return f"{parts[0]} - {parts[1]}"
        else:
            return parts[0]
    return textwrap.shorten(base_name, width=SHORT_FILENAME_MAX_LENGTH, placeholder="...")


def format_file_size(n):
    try:
        return format_decimal_filesize(int(n))
    except (TypeError, ValueError):
        return "0 bytes"


def seconds_to_time_str(seconds):
    try:
        hours = int(seconds // SECONDS_PER_HOUR)
        minutes = int(seconds % SECONDS_PER_HOUR // SECONDS_PER_MINUTE)
        secs = int(seconds % SECONDS_PER_MINUTE)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    except (TypeError, ValueError, OverflowError):
        return "00:00:00"


def format_date(date_string):
    try:
        return date_parser.parse(date_string).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OverflowError):
        return None
