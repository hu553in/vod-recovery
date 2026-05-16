import pytest

from vod_recovery import recovery
from vod_recovery.common import ReturnToMainError


def test_find_recoverable_m3u8_url_returns_none_on_unexpected_errors(monkeypatch, _quiet_output):
    async def broken_find(*_args):
        raise RuntimeError("boom")

    monkeypatch.setattr(recovery.playlist, "find_vod_playlist_url", broken_find)

    assert recovery.find_recoverable_m3u8_url("streamer", "123", "2026-05-15 14:58:12") is None


def test_find_recoverable_m3u8_url_preserves_return_to_main(monkeypatch):
    async def cancelled(*_args):
        raise ReturnToMainError

    monkeypatch.setattr(recovery.playlist, "find_vod_playlist_url", cancelled)

    with pytest.raises(ReturnToMainError):
        recovery.find_recoverable_m3u8_url("streamer", "123", "2026-05-15 14:58:12")


def test_recover_vod_prompts_for_missing_timestamp_and_selects_quality(monkeypatch, _quiet_output):
    monkeypatch.setattr(recovery.utils, "calculate_days_since_broadcast", lambda _timestamp: 0)
    monkeypatch.setattr(recovery, "ask_text", lambda _message: "2026-05-15 14:58:12")
    monkeypatch.setattr(recovery, "find_recoverable_m3u8_url", lambda *_args: "found-url")
    monkeypatch.setattr(recovery.playlist, "select_supported_quality", lambda url: f"{url}-best")

    assert recovery.recover_vod("streamer", "123", None) == "found-url-best"


def test_recover_vod_returns_none_when_quality_selection_fails(monkeypatch, _quiet_output):
    monkeypatch.setattr(recovery.utils, "calculate_days_since_broadcast", lambda _timestamp: 0)
    monkeypatch.setattr(recovery, "find_recoverable_m3u8_url", lambda *_args: "found-url")
    monkeypatch.setattr(recovery.playlist, "select_supported_quality", lambda _url: None)

    assert recovery.recover_vod("streamer", "123", "2026-05-15 14:58:12") is None
