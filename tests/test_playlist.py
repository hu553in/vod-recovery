import asyncio

import httpx

from vod_recovery import playlist

EXPECTED_SEARCH_OFFSET_COUNT = 90
SEARCH_LIMIT_UNDER_TEST = 2
LAST_AVAILABLE_SEGMENT = 2
EXPECTED_GENERATED_SEGMENTS = 3


class FakeResponse:
    def __init__(self, status_code, text="", content=b""):
        self.status_code = status_code
        self.text = text
        self.content = content

    def raise_for_status(self):
        raise httpx.HTTPError("server error")


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def request(self, method, url, timeout=None):
        self.calls.append((method, url, timeout))
        return self.responses.pop(0)

    async def get(self, url, timeout=None):
        self.calls.append(("GET", url, timeout))
        return self.responses.pop(0)


def test_iter_m3u8_search_offsets_starts_at_exact_timestamp_and_covers_range():
    offsets = list(playlist.iter_m3u8_search_offsets(-30, 60))

    assert offsets[:7] == [0, -1, 1, -2, 2, -3, 3]
    assert len(offsets) == EXPECTED_SEARCH_OFFSET_COUNT
    assert sorted(offsets) == list(range(-30, 60))


def test_build_candidate_m3u8_url_matches_twitch_hash_contract():
    url = playlist.build_candidate_m3u8_url(
        "https://vod.example/", "kuzma671", "318688040160", 1778857092, "chunked"
    )

    assert url == (
        "https://vod.example/"
        "e8f4a8b5b2c874439c8c_kuzma671_318688040160_1778857092/"
        "chunked/index-dvr.m3u8"
    )


def test_build_candidate_m3u8_urls_checks_nearest_seconds_first():
    urls = playlist.build_candidate_m3u8_urls(
        ["https://domain/"], ["chunked"], "streamer", "123", "2026-05-15 14:58:12"
    )

    assert "_1778857092/" in urls[0]
    assert "_1778857091/" in urls[1]
    assert "_1778857093/" in urls[2]


def test_fetch_status_accepts_valid_m3u8_response():
    session = FakeSession([FakeResponse(200, text="#EXTM3U\n#EXT-X-ENDLIST\n")])
    url = "https://example.com/index-dvr.m3u8"

    assert asyncio.run(playlist.fetch_status(session, url, retries=1, timeout=1)) == url
    assert session.calls == [("GET", url, 1)]


def test_fetch_status_falls_back_to_get_when_head_segment_is_blocked():
    session = FakeSession([FakeResponse(403), FakeResponse(200, content=b"segment")])
    url = "https://example.com/0.ts"

    assert asyncio.run(playlist.fetch_status(session, url, retries=1, timeout=1)) == url
    assert session.calls == [("HEAD", url, 1), ("GET", url, 1)]


def test_find_vod_playlist_url_limits_in_flight_tasks_and_stops_after_hit(
    monkeypatch, _quiet_output
):
    active = 0
    max_active = 0
    calls = []

    async def fake_fetch_status(_session, url, **_kwargs):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        calls.append(url)
        await asyncio.sleep(0.01)
        active -= 1
        return url if "/hit/" in url else None

    monkeypatch.setattr(playlist, "fetch_status", fake_fetch_status)
    monkeypatch.setattr(playlist, "M3U8_SEARCH_CONNECTOR_LIMIT", SEARCH_LIMIT_UNDER_TEST)
    monkeypatch.setattr(playlist, "M3U8_SEARCH_TOTAL_TIMEOUT_SECONDS", 2)
    monkeypatch.setattr(
        playlist,
        "build_candidate_m3u8_urls",
        lambda *_args: [
            "https://example.com/miss/0/index-dvr.m3u8",
            "https://example.com/miss/1/index-dvr.m3u8",
            "https://example.com/hit/2/index-dvr.m3u8",
            "https://example.com/inflight/3/index-dvr.m3u8",
            "https://example.com/not-started/4/index-dvr.m3u8",
        ],
    )

    result = asyncio.run(playlist.find_vod_playlist_url("streamer", "123", "2026-05-15 14:58:12"))

    assert result == "https://example.com/hit/2/index-dvr.m3u8"
    assert max_active <= SEARCH_LIMIT_UNDER_TEST
    assert "https://example.com/not-started/4/index-dvr.m3u8" not in calls


def test_find_vod_playlist_url_times_out_and_cancels_pending_tasks(monkeypatch, _quiet_output):
    warnings = []

    async def slow_fetch_status(*_args, **_kwargs):
        await asyncio.sleep(10)

    monkeypatch.setattr(playlist, "fetch_status", slow_fetch_status)
    monkeypatch.setattr(playlist, "M3U8_SEARCH_CONNECTOR_LIMIT", SEARCH_LIMIT_UNDER_TEST)
    monkeypatch.setattr(playlist, "M3U8_SEARCH_TOTAL_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(
        playlist, "print_warning", lambda message, **_kwargs: warnings.append(message)
    )

    result = asyncio.run(playlist.find_vod_playlist_url("streamer", "123", "2026-05-15 14:58:12"))

    assert result is None
    assert warnings == ["M3U8 search timed out after 0.05 seconds."]


def test_select_supported_quality_returns_chunked_without_network_when_always_best(monkeypatch):
    m3u8_link = "https://example.com/hash_streamer_123_1778857092/chunked/index-dvr.m3u8"
    monkeypatch.setattr(playlist, "get_config_value", lambda key: key == "always_best_quality")

    assert playlist.select_supported_quality(m3u8_link) == m3u8_link


def test_select_supported_quality_replaces_found_quality_with_best_available(
    monkeypatch, _quiet_output
):
    m3u8_link = "https://example.com/hash_streamer_123_1778857092/720p60/index-dvr.m3u8"
    monkeypatch.setattr(playlist, "get_config_value", lambda key: key == "always_best_quality")
    monkeypatch.setattr(playlist, "collect_valid_resolutions", lambda *_args: ["720p60", "1080p60"])
    monkeypatch.setattr(playlist, "get_chunked_resolution_info", lambda *_args: None)

    assert (
        playlist.select_supported_quality(m3u8_link)
        == "https://example.com/hash_streamer_123_1778857092/1080p60/index-dvr.m3u8"
    )


def test_generate_m3u8_from_segments_uses_binary_search_for_last_segment(
    monkeypatch, _quiet_output
):
    checked_urls = []

    def fake_request_with_retry(_method, url, **_kwargs):
        checked_urls.append(url)
        segment_number = int(url.rsplit("/", 1)[1].split(".", 1)[0])
        return FakeResponse(200 if segment_number <= LAST_AVAILABLE_SEGMENT else 404)

    monkeypatch.setattr(playlist, "request_with_retry", fake_request_with_retry)

    generated = playlist.generate_m3u8_from_segments("https://example.com/vod/")

    assert generated is not None
    assert generated.count("#EXTINF") == EXPECTED_GENERATED_SEGMENTS
    assert "0.ts" in generated
    assert "2.ts" in generated
    assert "3.ts" not in generated
    assert "https://example.com/vod/100.ts" in checked_urls


def test_process_m3u8_configuration_prefers_existing_generated_playlist_for_blocked_vod(
    monkeypatch, tmp_path
):
    generated = tmp_path / "vod_123_generated.m3u8"
    generated.write_text("#EXTM3U\n", encoding="utf-8")

    monkeypatch.setattr(playlist, "is_blocked_playlist", lambda _link: True)
    monkeypatch.setattr(playlist, "get_generated_playlist_path", lambda _vod_id: str(generated))

    assert playlist.process_m3u8_configuration(
        "https://example.com/hash_streamer_123_1778857092/chunked/index-dvr.m3u8"
    ) == str(generated)
