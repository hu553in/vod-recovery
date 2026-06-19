import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

import questionary
import tomli_w
import typed_settings as ts
from fake_useragent import UserAgent
from platformdirs import PlatformDirs, user_downloads_path
from rich.console import Console

APP_NAME = "vod-recovery"
CONFIG_DIR = PlatformDirs(APP_NAME, appauthor=False, ensure_exists=True).user_config_path
CONFIG_FILE = CONFIG_DIR / "settings.toml"
CONSOLE = Console(highlight=False)


def get_system_download_directory():
    return str(user_downloads_path())


def normalize_default_directory_value(default_directory):
    if not default_directory:
        return get_system_download_directory()
    if not isinstance(default_directory, str):
        return default_directory
    system_download_directory = Path(get_system_download_directory())
    if Path(default_directory).expanduser() == system_download_directory:
        return str(system_download_directory)
    return default_directory


@dataclass
class AppConfig:
    check_segments: bool = False
    always_best_quality: bool = False
    default_directory: str = field(default_factory=get_system_download_directory)
    default_video_format: str = ".mp4"
    playback_command: str = ""


@dataclass
class AppState:
    cached_user_agent: UserAgent | None = None
    cached_ffmpeg_path: str | None = None
    cached_ffprobe_path: str | None = None


class ReturnToMainError(Exception):
    pass


def return_to_main_menu():
    raise ReturnToMainError()


def ask_select(message, choices, default=None):
    answer = questionary.select(message, choices=choices, default=default).ask()
    if answer is None:
        return_to_main_menu()
    return answer


def ask_text(message, default=""):
    answer = questionary.text(message, default=default).ask()
    if answer is None:
        return_to_main_menu()
    return answer


def ask_path(message, default=""):
    answer = questionary.path(message, default=default).ask()
    if answer is None:
        return_to_main_menu()
    return answer


def ask_confirm(message, default=False):
    answer = questionary.confirm(message, default=default).ask()
    if answer is None:
        return_to_main_menu()
    return answer


def pause(message="Press Enter to continue..."):
    questionary.text(message, default="").ask()


def print_blank(lines=1):
    for _ in range(lines):
        CONSOLE.print()


def print_text(message="", *, style=None, before=0, after=0):
    print_blank(before)
    CONSOLE.print(message, style=style)
    print_blank(after)


def print_title(message, *, before=0, after=0):
    print_text(message, style="bold", before=before, after=after)


def print_progress(message):
    CONSOLE.file.write(f"\r{message}")
    CONSOLE.file.flush()


def print_error(message, *, before=0, after=0):
    print_text(message, style="red", before=before, after=after)


def print_info(message, *, before=0, after=0):
    print_text(message, style="cyan", before=before, after=after)


def print_success(message, *, before=0, after=0):
    print_text(message, style="green", before=before, after=after)


def print_warning(message, *, before=0, after=0):
    print_text(message, style="yellow", before=before, after=after)


def config_field_names():
    return {config_field.name for config_field in fields(AppConfig)}


def config_to_dict():
    return {
        config_field.name: getattr(CONFIG, config_field.name) for config_field in fields(AppConfig)
    }


def write_config_file():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "wb") as config_file:
        tomli_w.dump(config_to_dict(), config_file)


def prune_config_file():
    if not CONFIG_FILE.exists():
        return
    with open(CONFIG_FILE, "rb") as config_file:
        data = tomllib.load(config_file)
    if "playback_command" not in data and "media_player_location" in data:
        data["playback_command"] = data["media_player_location"]
    if "default_directory" in data:
        data["default_directory"] = normalize_default_directory_value(data["default_directory"])
    known_data = {key: value for key, value in data.items() if key in config_field_names()}
    if known_data != data:
        with open(CONFIG_FILE, "wb") as config_file:
            tomli_w.dump(known_data, config_file)


def load_config():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        prune_config_file()
        loaded_config = ts.load(
            AppConfig,
            APP_NAME,
            config_files=[CONFIG_FILE],
            config_file_section=None,
            config_files_var=None,
            env_prefix=None,
        )
    except Exception:
        loaded_config = AppConfig()
    for config_field in fields(AppConfig):
        setattr(CONFIG, config_field.name, getattr(loaded_config, config_field.name))
    write_config_file()


def get_config_value(key):
    if key not in config_field_names():
        return None
    return getattr(CONFIG, key)


def set_config_value(key, value):
    if key not in config_field_names():
        return
    setattr(CONFIG, key, value)
    write_config_file()


def get_default_video_format():
    default_video_format = get_config_value("default_video_format")
    if default_video_format in SUPPORTED_FORMATS:
        return default_video_format
    return ".mp4"


def get_ffmpeg_format(file_extension):
    format_map = {".mp4": "mp4", ".mkv": "matroska", ".ts": "mpegts", ".mov": "mov", ".avi": "avi"}
    return format_map.get(file_extension, "mp4")


def get_default_directory():
    default_directory = get_config_value("default_directory") or get_system_download_directory()
    directory = Path(default_directory).expanduser()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except (OSError, ValueError):
        directory = Path(get_system_download_directory())
        directory.mkdir(parents=True, exist_ok=True)
    return str(directory)


def get_data_path(filename):
    return str(PACKAGE_DIR / "data" / filename)


SUPPORTED_FORMATS = [".mp4", ".mkv", ".mov", ".avi", ".ts"]
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
RESOLUTIONS = [
    "chunked",
    "2160p60",
    "2160p30",
    "2160p20",
    "1440p60",
    "1440p30",
    "1440p20",
    "1080p60",
    "1080p30",
    "1080p20",
    "720p60",
    "720p30",
    "720p20",
    "480p60",
    "480p30",
    "360p60",
    "360p30",
    "160p60",
    "160p30",
]

CONFIG = AppConfig()
APP_STATE = AppState()
PACKAGE_DIR = Path(__file__).resolve().parent

HELP_DATA = {
    "Main menu": {
        "Recover hidden VOD": "Find a playable M3U8 playlist for a hidden Twitch VOD.",
        "Download M3U8": "Download a known M3U8 URL or local playlist file.",
        "Settings": "Change saved defaults.",
        "Help": "Show this help text.",
        "Exit": "Exit the app.",
    },
    "Settings": {
        "Validate segments": "Check playlist segment availability before downloading.",
        "Best quality": "Select the best available quality automatically.",
        "Download format": "Output container format.",
        "Output directory": "Download output directory.",
        "Playback command": (
            "Command or path used for playback. Auto tries ffplay, mpv, VLC, and MPC-HC."
        ),
        "Back": "Return to the previous menu.",
    },
}
