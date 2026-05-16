from pathlib import Path
from urllib.parse import urlparse

import questionary

from . import playback
from .common import (
    CONFIG,
    HELP_DATA,
    SUPPORTED_FORMATS,
    ask_confirm,
    ask_path,
    ask_select,
    ask_text,
    get_default_directory,
    get_default_video_format,
    print_success,
    print_text,
    print_title,
    print_warning,
    set_config_value,
)


def bool_label(value):
    return "on" if value else "off"


def select_main_action():
    default_format = get_default_video_format().lstrip(".") or "mp4"
    return ask_select(
        "Main menu:",
        choices=[
            questionary.Choice("Recover hidden VOD", "recover"),
            questionary.Choice(f"Download M3U8 ({default_format})", "download"),
            questionary.Choice("Settings", "settings"),
            questionary.Choice("Help", "help"),
            questionary.Choice("Exit", "exit"),
        ],
    )


def select_m3u8_source():
    return ask_select(
        "M3U8 source:",
        choices=[
            questionary.Choice("M3U8 URL", "url"),
            questionary.Choice("Local M3U8 file", "file"),
            questionary.Choice("Back", "back"),
        ],
    )


def select_settings_action():
    return ask_select(
        "Settings:",
        choices=[
            questionary.Choice(
                f"Validate segments: {bool_label(CONFIG.check_segments)}", "check_segments"
            ),
            questionary.Choice(
                f"Best quality: {bool_label(CONFIG.always_best_quality)}", "always_best_quality"
            ),
            questionary.Choice(
                f"Download format: {get_default_video_format().lstrip('.') or 'mp4'}", "format"
            ),
            questionary.Choice(f"Output directory: {get_default_directory()}", "directory"),
            questionary.Choice(
                f"Playback command: {CONFIG.playback_command or 'auto'}", "playback"
            ),
            questionary.Choice("Back", "back"),
        ],
    )


def ask_m3u8_url():
    while True:
        m3u8_url = ask_text("M3U8 URL:").strip(" \"'")
        parsed_url = urlparse(m3u8_url)
        if parsed_url.scheme in {"http", "https"} and parsed_url.path.endswith(".m3u8"):
            return m3u8_url
        print_warning("Enter a valid M3U8 URL.", after=1)


def show_help():
    print_title("Help", before=1)
    for menu, options in HELP_DATA.items():
        print_title(f"{menu}:", before=1)
        for option, description in options.items():
            print_text(f"  {option}: {description}")
    print_text()


def set_check_segments():
    enabled = ask_confirm("Validate segments before downloading?", CONFIG.check_segments)
    set_config_value("check_segments", enabled)
    print_success(f"Validate segments: {bool_label(enabled)}", before=1)


def set_always_best_quality():
    enabled = ask_confirm(
        "Select the best available quality automatically?", CONFIG.always_best_quality
    )
    set_config_value("always_best_quality", enabled)
    print_success(f"Best quality: {bool_label(enabled)}", before=1)


def set_default_video_format():
    selected_format = ask_select(
        "Download format:",
        choices=[
            questionary.Choice(format_option.lstrip("."), format_option)
            for format_option in SUPPORTED_FORMATS
        ]
        + [questionary.Choice("Back", "back")],
        default=get_default_video_format(),
    )
    if selected_format == "back":
        return
    set_config_value("default_video_format", selected_format)
    print_success(f"Download format: {selected_format.lstrip('.')}", before=1)


def set_default_directory():
    file_path = ask_path("Output directory:", default=get_default_directory()).strip(" \"'")
    while True:
        if not file_path:
            print_warning("No directory entered.", before=1)
            return
        directory = Path(file_path).expanduser()
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            file_path = ask_path(f"Could not create directory: {e}. Output directory:").strip(
                " \"'"
            )
            continue
        file_path = str(directory)
        set_config_value("default_directory", file_path)
        print_success(f"Output directory: {file_path}", before=1)
        break


def set_playback_command():
    command = ask_text(
        "Playback command (blank for auto):", default=CONFIG.playback_command
    ).strip()
    while command and not playback.parse_playback_command(command):
        print_warning(
            "Playback command not found. Use ffplay, mpv, VLC/MPC-HC, or leave blank.", before=1
        )
        command = ask_text("Playback command (blank for auto):", default=command).strip()
    set_config_value("playback_command", command)
    print_success(f"Playback command: {command or 'auto'}", before=1)


def ask_m3u8_file_path():
    file_path = ask_path("M3U8 file path:", default=get_default_directory()).strip(" \"'")
    if not file_path:
        return None
    file_path = str(Path(file_path).expanduser())
    while not Path(file_path).exists():
        file_path = ask_path("M3U8 file not found. Path:").strip(" \"'")
        file_path = str(Path(file_path).expanduser())
    return file_path


def select_download_action(has_player):
    choices = [questionary.Choice("Download", "download")]
    if has_player:
        choices.append(questionary.Choice("Play", "play"))
    choices.append(questionary.Choice("Back", "back"))
    return ask_select("Action:", choices=choices)
