import tomllib

import tomli_w

from vod_recovery import common, playback


def test_get_default_video_format_falls_back_to_mp4(monkeypatch):
    monkeypatch.setattr(common.CONFIG, "default_video_format", ".bad")

    assert common.get_default_video_format() == ".mp4"


def test_get_default_directory_falls_back_to_system_downloads_on_invalid_config(
    monkeypatch, tmp_path
):
    fallback = tmp_path / "Downloads"
    monkeypatch.setattr(common.CONFIG, "default_directory", "\0")
    monkeypatch.setattr(common, "get_system_download_directory", lambda: str(fallback))

    assert common.get_default_directory() == str(fallback)
    assert fallback.is_dir()


def test_prune_config_file_migrates_legacy_playback_key_and_drops_unknowns(monkeypatch, tmp_path):
    config_file = tmp_path / "settings.toml"
    config_file.write_bytes(
        tomli_w.dumps(
            {"media_player_location": "mpv", "default_directory": str(tmp_path), "unknown": "drop"}
        ).encode()
    )
    monkeypatch.setattr(common, "CONFIG_FILE", config_file)

    common.prune_config_file()

    pruned = tomllib.loads(config_file.read_text(encoding="utf-8"))
    assert pruned == {"default_directory": str(tmp_path), "playback_command": "mpv"}


def test_parse_playback_command_resolves_executable_and_preserves_arguments(monkeypatch):
    def normalize_command_path(value):
        return "/usr/bin/mpv" if value == "mpv" else None

    monkeypatch.setattr(playback, "normalize_command_path", normalize_command_path)

    command = playback.parse_playback_command(
        'mpv --force-window=yes "https://example.com/vod.m3u8"'
    )

    assert command == playback.PlaybackCommand(
        ("/usr/bin/mpv", "--force-window=yes", "https://example.com/vod.m3u8")
    )


def test_parse_playback_command_rejects_bad_shell_syntax():
    assert playback.parse_playback_command('"unterminated') is None


def test_find_playback_command_on_path_adds_ffplay_default_args(monkeypatch):
    def fake_which(command):
        return f"/usr/bin/{command}" if command == "ffplay" else None

    monkeypatch.setattr(playback.shutil, "which", fake_which)

    assert playback.find_playback_command_on_path() == playback.PlaybackCommand(
        ("/usr/bin/ffplay", "-autoexit")
    )


def test_normalize_media_target_returns_non_windows_targets_unchanged(tmp_path):
    media = tmp_path / "video.m3u8"
    media.write_text("#EXTM3U\n", encoding="utf-8")

    assert playback.normalize_media_target(str(media)) == str(media)


def test_open_media_for_app_bundle_uses_macos_open(monkeypatch):
    popen_calls = []
    command = playback.PlaybackCommand(("/Applications/VLC.app", "--fullscreen"), app_bundle=True)

    def find_command(_command_name):
        return "/usr/bin/open"

    def record_popen(args):
        popen_calls.append(args)

    monkeypatch.setattr(playback.shutil, "which", find_command)
    monkeypatch.setattr(playback.subprocess, "Popen", record_popen)

    playback.open_media(command, "https://example.com/vod.m3u8")

    assert popen_calls == [
        [
            "/usr/bin/open",
            "-a",
            "/Applications/VLC.app",
            "https://example.com/vod.m3u8",
            "--args",
            "--fullscreen",
        ]
    ]
