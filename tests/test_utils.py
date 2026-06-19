from datetime import UTC, datetime

import pytest

from vod_recovery import utils

EXPECTED_EPOCH_WITH_OFFSET = 1_778_857_093


def test_format_iso_datetime_normalizes_timezone_to_utc_naive():
    assert utils.format_iso_datetime("2026-05-15T20:58:12+06:00") == "2026-05-15 14:58:12"


@pytest.mark.parametrize("value", ["", "not-a-date", None])
def test_format_iso_datetime_rejects_invalid_values(value):
    assert utils.format_iso_datetime(value) is None


def test_calculate_epoch_timestamp_applies_second_offset():
    assert utils.calculate_epoch_timestamp("2026-05-15 14:58:12", 1) == EXPECTED_EPOCH_WITH_OFFSET


def test_parse_twitch_vod_path_keeps_streamer_names_with_underscores():
    parsed = utils.parse_twitch_vod_path(
        "https://vod-secure.twitch.tv/hash_some_streamer_12345_1778857092/chunked/index-dvr.m3u8"
    )

    assert parsed == {
        "streamer_name": "some_streamer",
        "video_id": "12345",
        "timestamp": "1778857092",
    }


def test_parse_twitch_vod_path_rejects_unrecognized_urls():
    with pytest.raises(ValueError, match="Could not parse Twitch VOD path"):
        utils.parse_twitch_vod_path("https://example.com/not-a-vod/index-dvr.m3u8")


def test_sanitize_filename_collapses_replacement_underscores():
    assert utils.sanitize_filename(" bad:/name*? ") == "bad_name"


def test_parse_vod_filename_uses_last_underscore_as_video_id(_quiet_output):
    assert utils.parse_vod_filename("/tmp/some_streamer_12345.m3u8") == ("some_streamer", "12345")  # nosec B108


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, "00:00:00"), (3661, "01:01:01"), (None, "00:00:00"), ("bad", "00:00:00")],
)
def test_seconds_to_time_str(seconds, expected):
    assert utils.seconds_to_time_str(seconds) == expected


def test_calculate_days_since_broadcast_never_returns_negative():
    future = datetime.now(UTC).replace(tzinfo=None).replace(year=datetime.now(UTC).year + 1)
    assert utils.calculate_days_since_broadcast(future.strftime("%Y-%m-%d %H:%M:%S")) == 0
