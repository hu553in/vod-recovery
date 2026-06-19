# VOD recovery

[![CI](https://github.com/hu553in/vod-recovery/actions/workflows/ci.yml/badge.svg)](https://github.com/hu553in/vod-recovery/actions/workflows/ci.yml)

Interactive CLI for finding recoverable hidden Twitch VOD playlists and downloading them with
`ffmpeg`.

The app asks for a streamer name, finds recent streams, searches for a playable VOD playlist, lets
you choose quality, and downloads the result.

## What it does

- Finds recent Twitch streams by streamer name
- Searches recoverable hidden VOD playlists
- Lists streams in an interactive paginated menu
- Lets you choose an available quality, or always selects the best quality
- Downloads M3U8 URLs and local playlist files
- Rebuilds playlists with absolute segment URLs when needed
- Tries to recover unmuted segments automatically
- Optionally validates playlist segments before downloading
- Plays playlists with a known HLS-capable command when one is available

## Requirements

- Python 3.13+
- `uv`
- Network access to Twitch and playlist hosts
- Optional: `ffmpeg` and `ffprobe` available in `PATH`
- Optional: `ffplay`, `mpv`, VLC, or MPC-HC for playback from the menu

`ffmpeg` and `ffprobe` are resolved from `PATH` first. If they are not installed locally, the app
uses `static-ffmpeg` as a fallback.

Additional requirements for specific workflows:

- `git` - required for `uv tool install git+...` and `uvx --from git+...`
- `make` - required for development commands

## Installation

### `uv tool`

Useful commands:

```bash
uv tool install .
uv tool install git+https://github.com/hu553in/vod-recovery.git
vod-recovery
uv tool upgrade vod-recovery
uv tool uninstall vod-recovery
```

### `uvx`

Useful commands:

```bash
uvx --from . vod-recovery
uvx --from git+https://github.com/hu553in/vod-recovery.git vod-recovery
```

### Development checkout

Useful commands:

```bash
make install-deps
uv run vod-recovery
make test
make check
```

## Settings

Settings are stored in the platform-specific user config directory provided by `platformdirs`. The
`settings.toml` file is created with defaults on first run.

| Name                   | Default                  | Description                                      |
| ---------------------- | ------------------------ | ------------------------------------------------ |
| `check_segments`       | `false`                  | Validate segment availability before downloading |
| `always_best_quality`  | `false`                  | Select the best available quality automatically  |
| `default_directory`    | user downloads directory | Download output directory                        |
| `default_video_format` | `.mp4`                   | Output container format                          |
| `playback_command`     | `""`                     | Playback command or path; blank means auto       |

Supported output containers: `.mp4`, `.mkv`, `.mov`, `.avi`, `.ts`.

### Playback

When `playback_command` is blank, the app tries known HLS-capable players in this order:

```text
ffplay -> mpv -> vlc -> mpc-hc64 -> mpc-hc
```

You can set `playback_command` to a command, command with arguments, executable path, or macOS
`.app` bundle path.

Examples:

```text
ffplay
mpv --force-window=yes
/Applications/VLC.app
```

The app does not use the operating system's default media application because many default apps
cannot play remote HLS/M3U8 streams reliably.

## Runtime behavior

### VOD recovery

VOD recovery searches known Twitch VOD playlist domains around the stream start timestamp. The app
checks the exact timestamp first, then nearby seconds. It warns when a stream is older than 60 days
because recovery is usually unlikely after that point. The M3U8 search stops after 60 seconds.

### Playlist processing

Before downloading, the app can:

- select an available quality
- rewrite playlist segment URLs to absolute URLs
- build a local playlist for unmuted segments
- validate segment availability when `check_segments` is enabled

## Notes

- Recovery depends on playlist and segment availability outside this project.
- Twitch and third-party metadata endpoints can change without notice.
- The app is local and single-user in scope.
- Generated playlists and downloads are written only under the configured output directory.
