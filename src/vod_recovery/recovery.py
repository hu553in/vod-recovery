import asyncio

from . import playlist, utils
from .common import (
    ReturnToMainError,
    ask_text,
    pause,
    print_error,
    print_info,
    print_warning,
    return_to_main_menu,
)

MAX_RECOVERABLE_VOD_AGE_DAYS = 60


def find_recoverable_m3u8_url(streamer_name, video_id, timestamp):
    try:
        return asyncio.run(playlist.find_vod_playlist_url(streamer_name, video_id, timestamp))
    except ReturnToMainError:
        raise
    except Exception as e:
        print_error(f"VOD recovery failed: {e!s}", before=1)
        return None


def recover_vod(streamer_name, video_id, timestamp):
    print_info(f"Stream time: {timestamp}", before=1)
    try:
        vod_age = utils.calculate_days_since_broadcast(timestamp)
        if vod_age > MAX_RECOVERABLE_VOD_AGE_DAYS:
            print_warning("VOD is older than 60 days; recovery is unlikely.")
        if not timestamp:
            print_error("Could not determine the stream start time.", before=1)
            timestamp = ask_text("Stream start time (YYYY-MM-DD HH:MM:SS):")
            if not timestamp:
                print_error("No stream time entered.", before=1)
                pause()
                return_to_main_menu()
        m3u8_url = find_recoverable_m3u8_url(streamer_name, video_id, timestamp)
        vod_url = playlist.select_supported_quality(m3u8_url)
        if vod_url is None:
            print_error("VOD not found.", before=1)
            return None
        return vod_url
    except ReturnToMainError:
        raise
    except Exception as e:
        print_error(f"VOD recovery failed: {e!s}", before=1)
        return None
