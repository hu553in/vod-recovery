# VOD recovery

[![CI](https://github.com/hu553in/vod-recovery/actions/workflows/ci.yml/badge.svg)](https://github.com/hu553in/vod-recovery/actions/workflows/ci.yml)

Interactive CLI for finding recoverable hidden Twitch VOD playlists and downloading them with
`ffmpeg`.

## What it does

- Finds recent Twitch streams by streamer name
- Searches known VOD playlist domains around the stream start timestamp
- Lets the user select a stream and quality
- Downloads remote M3U8 URLs or local playlists
- Rewrites segment URLs when needed
- Can recover unmuted segments and validate segment availability
- Can open playlists in an HLS-capable player

## Requirements

- Python 3.13+
- `uv`
- Network access to Twitch and playlist hosts
- Optional: `ffmpeg` and `ffprobe` in `PATH`
- Optional: `ffplay`, `mpv`, VLC, or MPC-HC for playback

If `ffmpeg` or `ffprobe` is not installed locally, `static-ffmpeg` is used as a fallback.

## Setup

Install as a `uv` tool:

```bash
uv tool install git+https://github.com/hu553in/vod-recovery.git
```

Or run without installing:

```bash
uvx --from git+https://github.com/hu553in/vod-recovery.git vod-recovery
```

## Configuration

Settings are stored in the platform-specific user config directory. `settings.toml` is created with
defaults on first run.

| Name                   | Default                  | Description                                      |
| ---------------------- | ------------------------ | ------------------------------------------------ |
| `check_segments`       | `false`                  | Validate segment availability before downloading |
| `always_best_quality`  | `false`                  | Select the best available quality automatically  |
| `default_directory`    | user downloads directory | Download output directory                        |
| `default_video_format` | `.mp4`                   | Output container format                          |
| `playback_command`     | `""`                     | Playback command or path, blank means auto       |

Supported output containers: `.mp4`, `.mkv`, `.mov`, `.avi`, `.ts`.

When `playback_command` is blank, players are tried in this order:

```text
ffplay -> mpv -> vlc -> mpc-hc64 -> mpc-hc
```

## Usage

```bash
vod-recovery
```

From a checkout:

```bash
uv run vod-recovery
```

The app asks for a streamer, lists recent streams, searches for a recoverable playlist, lets you
choose quality, and downloads the result.

## Runtime behavior

- Searches the exact stream timestamp first, then nearby seconds
- Warns when a stream is older than 60 days
- Stops M3U8 search after 60 seconds
- Writes generated playlists and downloads only under the configured output directory
- Uses explicit playback commands because many default media apps cannot play remote HLS reliably

## Development

```bash
make install-deps
make check
```

Focused checks:

```bash
make lint
make check-types
make test
```
