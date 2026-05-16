import contextlib
import os
import shlex
import shutil
import subprocess  # nosec B404
import sys
from dataclasses import dataclass
from pathlib import Path

from .common import get_config_value, print_error

if sys.platform == "win32":
    import winreg
else:
    winreg = None

PLAYBACK_COMMANDS = ("ffplay", "mpv", "vlc", "mpc-hc64", "mpc-hc")
FFPLAY_DEFAULT_ARGS = ("-autoexit",)
MACOS_PLAYBACK_BUNDLE_IDS = ("io.mpv", "org.videolan.vlc")
WINDOWS_PLAYBACK_APP_NAMES = ("mpv.exe", "vlc.exe", "mpc-hc64.exe", "mpc-hc.exe")


@dataclass(frozen=True)
class PlaybackCommand:
    argv: tuple[str, ...]
    app_bundle: bool = False


def is_macos_app_bundle(command_path):
    path = Path(command_path)
    return sys.platform.startswith("darwin") and path.suffix == ".app" and path.is_dir()


def normalize_command_path(command_path):
    if not command_path:
        return None

    discovered_path = shutil.which(command_path)
    if discovered_path:
        return discovered_path

    expanded_path = Path(command_path).expanduser()
    if expanded_path.is_file() or is_macos_app_bundle(expanded_path):
        return str(expanded_path)

    return None


def parse_playback_command(raw_command):
    if not raw_command:
        return None

    if command_path := normalize_command_path(raw_command):
        return PlaybackCommand((command_path,), is_macos_app_bundle(command_path))

    try:
        command_parts = shlex.split(raw_command, posix=os.name != "nt")
    except ValueError:
        return None

    if not command_parts:
        return None

    command_path = normalize_command_path(command_parts[0])
    if not command_path:
        return None

    return PlaybackCommand((command_path, *command_parts[1:]), is_macos_app_bundle(command_path))


def find_playback_command_on_path():
    for command in PLAYBACK_COMMANDS:
        if command_path := shutil.which(command):
            command_args = FFPLAY_DEFAULT_ARGS if command == "ffplay" else ()
            return PlaybackCommand((command_path, *command_args))
    return None


def find_macos_playback_app():
    if not sys.platform.startswith("darwin"):
        return None

    mdfind_path = shutil.which("mdfind")
    if not mdfind_path:
        return None

    for bundle_id in MACOS_PLAYBACK_BUNDLE_IDS:
        try:
            result = subprocess.run(  # nosec B603
                [mdfind_path, f'kMDItemCFBundleIdentifier == "{bundle_id}"'],
                capture_output=True,
                check=False,
                text=True,
                timeout=2,
            )
        except (OSError, subprocess.SubprocessError):
            continue

        for line in result.stdout.splitlines():
            if command_path := normalize_command_path(line.strip()):
                return PlaybackCommand((command_path,), app_bundle=True)

    return None


def find_windows_playback_app():
    if winreg is None:
        return None

    root_keys = (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE)
    access_modes = (
        winreg.KEY_READ,
        winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0),
        winreg.KEY_READ | getattr(winreg, "KEY_WOW64_32KEY", 0),
    )

    for root_key in root_keys:
        for app_name in WINDOWS_PLAYBACK_APP_NAMES:
            app_path_key = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{app_name}"
            for access_mode in access_modes:
                with contextlib.suppress(OSError):
                    with winreg.OpenKey(root_key, app_path_key, 0, access_mode) as key:
                        command_path, _ = winreg.QueryValueEx(key, "")
                    if normalized_path := normalize_command_path(command_path):
                        return PlaybackCommand((normalized_path,))

    return None


def get_playback_command():
    configured_command = get_config_value("playback_command")
    if command := parse_playback_command(configured_command):
        return command
    return (
        find_playback_command_on_path() or find_macos_playback_app() or find_windows_playback_app()
    )


def normalize_media_target(media_target):
    if os.name == "nt" and Path(media_target).is_file():
        return media_target.replace("/", "\\")
    return media_target


def open_media(command, media_target):
    normalized_target = normalize_media_target(media_target)
    if command.app_bundle:
        open_path = shutil.which("open")
        if not open_path:
            print_error("macOS open command not found.", before=1)
            return
        app_args = ("--args", *command.argv[1:]) if len(command.argv) > 1 else ()
        subprocess.Popen(  # nosec B603
            [open_path, "-a", command.argv[0], normalized_target, *app_args]
        )
        return

    subprocess.Popen([*command.argv, normalized_target])  # nosec B603
