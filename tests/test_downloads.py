from types import SimpleNamespace

from vod_recovery import downloads


def test_build_vod_filename_sanitizes_title_and_includes_date(monkeypatch):
    monkeypatch.setattr(downloads, "get_default_video_format", lambda: ".mkv")

    assert (
        downloads.build_vod_filename(
            "streamer", "123", title="bad:/title*?", stream_date="2026-05-15T14:58:12Z"
        )
        == "streamer - 2026-05-15 - bad_title - [123].mkv"
    )


def test_get_filename_for_url_source_falls_back_for_unrecognized_url(monkeypatch, _quiet_output):
    monkeypatch.setattr(downloads, "get_default_video_format", lambda: ".mp4")

    assert (
        downloads.get_filename_for_url_source(
            "https://example.com/video.m3u8", title="Title", stream_date="2026-05-15"
        )
        == "video - 2026-05-15 - Title - [vod].mp4"
    )


def test_get_stream_date_from_m3u8_subtracts_twitch_total_seconds(tmp_path):
    playlist_path = tmp_path / "streamer_123.m3u8"
    playlist_path.write_text(
        "\n".join(
            ["#EXTM3U", "#ID3-EQUIV-TDTG:2026-05-16T01:30:00Z", "#EXT-X-TWITCH-TOTAL-SECS:9000"]
        ),
        encoding="utf-8",
    )

    assert downloads.get_stream_date_from_m3u8(playlist_path) == "2026-05-15"


def test_download_m3u8_source_deletes_local_playlist_after_success(
    monkeypatch, tmp_path, _quiet_output
):
    playlist_path = tmp_path / "streamer_123.m3u8"
    playlist_path.write_text("#EXTM3U\n", encoding="utf-8")
    calls = []

    def fake_download_m3u8_video_file(m3u8_file_path, output_filename):
        calls.append((m3u8_file_path, output_filename))
        return True

    monkeypatch.setattr(downloads, "download_m3u8_video_file", fake_download_m3u8_video_file)
    monkeypatch.setattr(downloads, "get_default_video_format", lambda: ".mp4")
    monkeypatch.setattr(downloads, "get_output_path", lambda filename: str(tmp_path / filename))

    assert downloads.download_m3u8_source(str(playlist_path), title="Title") is True
    assert calls == [(str(playlist_path), "streamer - Title - [123].mp4")]
    assert not playlist_path.exists()


def test_update_download_progress_uses_duration_percent_fields(tmp_path):
    output_path = tmp_path / "video.mp4"
    output_path.write_bytes(b"1" * 1024)
    updates = []

    class FakeProgress:
        def update(self, task_id, **kwargs):
            updates.append((task_id, kwargs))

    state = SimpleNamespace(
        rich_progress=FakeProgress(),
        task_id=7,
        open_ended=False,
        duration=120,
        output_path=str(output_path),
    )

    downloads.update_download_progress(state, 30)

    assert updates == [(7, {"time_info": "00:00:30/00:02:00 | 1.0 kB", "size": ""})]


def test_command_arguments_handles_plain_command_lists():
    assert downloads.command_arguments(["ffmpeg", "-i", "input"]) == ["ffmpeg", "-i", "input"]
