import sys

from . import downloads, playlist, prompts, sources
from .common import (
    ReturnToMainError,
    pause,
    print_blank,
    print_text,
    print_title,
    return_to_main_menu,
)


def run_interactive_app():
    print_title("VOD recovery", before=1)
    while True:
        try:
            action = prompts.select_main_action()
            if action == "recover":
                sources.recover_recent_streams()
            elif action == "download":
                download_existing_m3u8()
            elif action == "settings":
                show_settings()
            elif action == "help":
                prompts.show_help()
                pause()
            elif action == "exit":
                print_text("Exiting.", before=1, after=1)
                sys.exit()
        except ReturnToMainError:
            continue


def download_existing_m3u8():
    while True:
        source = prompts.select_m3u8_source()
        if source == "url":
            m3u8_url = prompts.ask_m3u8_url()
            print_blank()
            m3u8_source = playlist.process_m3u8_configuration(m3u8_url)
            downloads.handle_download_menu(m3u8_source)
        elif source == "file":
            file_path = prompts.ask_m3u8_file_path()
            if not file_path:
                print_text("No file selected.", before=1)
                return
            print_text(file_path, before=1, after=1)
            downloads.handle_file_download_menu(file_path.strip())
            pause()
            return
        elif source == "back":
            return


def show_settings():
    while True:
        action = prompts.select_settings_action()
        if action == "check_segments":
            prompts.set_check_segments()
        elif action == "always_best_quality":
            prompts.set_always_best_quality()
        elif action == "format":
            prompts.set_default_video_format()
        elif action == "directory":
            prompts.set_default_directory()
        elif action == "playback":
            prompts.set_playback_command()
        elif action == "back":
            return
        else:
            return_to_main_menu()
