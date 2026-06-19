from vod_recovery import sources


def test_extract_vod_id_from_preview_finds_video_id_and_timestamp():
    preview_url = (
        "https://static-cdn.jtvnw.net/cf_vods/hash_channel_name_318688040160_1778857092/thumb.jpg"
    )

    assert sources.extract_vod_id_from_preview("channel_name", preview_url) == (
        "318688040160",
        "1778857092",
    )


def test_merge_stream_sources_deduplicates_by_stream_id_and_sorts_newest_first():
    api_streams = [
        {"stream_id": "old", "dt_utc": "2026-05-01 10:00:00", "title": "old api", "duration": 1.0},
        {
            "stream_id": "same",
            "dt_utc": "2026-05-02 10:00:00",
            "title": "api title",
            "duration": 2.0,
        },
    ]
    vod_streams = [
        {
            "stream_id": "same",
            "dt_utc": "2026-05-03 10:00:00",
            "title": "vod title",
            "duration": 3.0,
        }
    ]

    merged = sources.merge_stream_sources(api_streams, vod_streams)

    assert merged == [
        {
            "stream_id": "same",
            "dt_utc": "2026-05-03 10:00:00",
            "title": "vod title",
            "duration": 3.0,
        },
        {"stream_id": "old", "dt_utc": "2026-05-01 10:00:00", "title": "old api", "duration": 1.0},
    ]


def test_normalize_streams_filters_missing_and_none_ids():
    normalized = sources.normalize_streams(
        [
            {
                "stream_id": 123,
                "dt_local": "2026-05-15 20:58:12",
                "dt_utc": "2026-05-15 14:58:12",
                "title": "title",
                "duration": 2.8,
            },
            {"stream_id": "none", "dt_local": "x", "dt_utc": "x", "title": "bad"},
            {"dt_local": "x", "dt_utc": "x", "title": "missing"},
        ]
    )

    assert normalized == [
        {
            "video_id": "123",
            "date_local": "2026-05-15 20:58:12",
            "date_utc": "2026-05-15 14:58:12",
            "title": "title",
            "duration": 2.8,
        }
    ]


def test_get_page_streams_uses_one_based_pages():
    streams = [{"id": value} for value in range(60)]

    assert sources.get_page_streams(streams, 2) == [{"id": value} for value in range(50, 60)]


def test_parse_stream_timestamp_reports_invalid_input(_quiet_output):
    assert sources.parse_stream_timestamp("bad timestamp") is None
