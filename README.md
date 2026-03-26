# QobuzProxy

A bridge between Qobuz Connect and DLNA speakers. Also supports local audio playback.

## Why?

Qobuz has a "Connect" feature (similar to Spotify Connect) that lets you control playback on supported devices from their app. Unfortunately, many popular speakers — most notably **Sonos** — don't support Qobuz Connect natively. This means you can't pick a Sonos speaker as a playback target in the Qobuz app, even though Sonos fully supports DLNA/UPnP streaming.

QobuzProxy solves this by acting as a virtual Qobuz Connect device on your network. When you open the Qobuz app, QobuzProxy shows up as a selectable speaker. When you play music, it receives the stream from Qobuz and forwards it to your DLNA-compatible speaker (like Sonos), preserving hi-res audio quality.

**In short:** Run QobuzProxy on a Raspberry Pi (or Docker or any always-on machine) on your local network, and your Sonos speakers become fully controllable Qobuz Connect targets — play, pause, skip, and adjust volume, all from the official Qobuz app.

## Features

- Appears as a Qobuz Connect device in the official Qobuz app
- Streams audio to DLNA renderers (Sonos, Denon HEOS, etc.)
- Local audio playback via PortAudio (play directly through your machine's speakers/DAC)
- Auto-detects device capabilities to select optimal audio quality
- Runs on Raspberry Pi, Docker, or any Linux/macOS system

## Audio Quality

By default (`max_quality: auto`), QobuzProxy queries your DLNA device's capabilities and automatically selects the best supported quality. You can also set a specific quality level:

| Value | Format |
|-------|--------|
| `auto` | Auto-detect from device (recommended) |
| `5` | MP3 320 kbps |
| `6` | FLAC CD (16-bit/44.1kHz) |
| `7` | FLAC Hi-Res (24-bit/96kHz) |
| `27` | FLAC Hi-Res (24-bit/192kHz) |

## Local Audio Playback

QobuzProxy can also play audio directly through your machine's speakers or DAC, without needing a DLNA device. Set the `QOBUZPROXY_BACKEND` environment variable to `local`:

```bash
docker run -d --network host \
  -e QOBUZ_EMAIL=your@email.com \
  -e QOBUZ_PASSWORD=yourpassword \
  -e QOBUZPROXY_BACKEND=local \
  --device /dev/snd \
  ghcr.io/leolobato/qobuz-proxy:latest
```

Note: The `--device /dev/snd` flag gives the container access to the host's audio devices (Linux only).

## Installation

A pre-built Docker image is available from GitHub Container Registry:

```bash
docker pull ghcr.io/leolobato/qobuz-proxy:latest
```

### Quick Start

1. Copy `.env.example` to `.env` and fill in your credentials:
   ```bash
   cp .env.example .env
   nano .env  # Edit with your values
   ```

2. Run with the pre-built image:
   ```bash
   docker run -d --network host --env-file .env ghcr.io/leolobato/qobuz-proxy:latest
   ```

   Or build and run locally with Docker Compose:
   ```bash
   docker-compose up -d
   ```

3. View logs:
   ```bash
   docker-compose logs -f
   ```

### Network Requirements

**Important**: QobuzProxy requires `network_mode: host` for mDNS discovery to work. This allows the Qobuz app to find the device on your local network.

If you cannot use host networking, consider:
- Using a macvlan network with a dedicated IP on your LAN
- Running QobuzProxy directly on the host (not in Docker)

### Configuration

Configuration can be provided via:

1. **Environment variables** (recommended for Docker):
   Set variables in `.env` file or directly in `docker-compose.yaml`

2. **Config file** (mounted volume):
   ```yaml
   volumes:
     - ./config.yaml:/app/config.yaml:ro
   ```

3. **Credentials cache** (optional, persists Qobuz app credentials):
   ```yaml
   volumes:
     - /path/to/cache:/home/qobuzproxy/.qobuz-proxy
   ```
   This caches the Qobuz web player credentials so they don't need to be re-scraped on each restart. The container runs as user `qobuzproxy`, so the path is `/home/qobuzproxy/.qobuz-proxy` (not `/root/.qobuz-proxy`).

### Ports

| Port | Purpose |
|------|---------|
| 8689 | HTTP server for mDNS discovery |
| 7120 | Audio proxy for DLNA streaming |

With `network_mode: host`, these ports are exposed directly on the host.

### Health Check

The container includes a health check that verifies the HTTP server is responding:
```bash
docker inspect --format='{{.State.Health.Status}}' qobuz-proxy
```

## Acknowledgments

This project is based on the Qobuz Connect reverse-engineering work done by [Tobias Guyer](https://github.com/tobiasguyer) in [StreamCore32](https://github.com/tobiasguyer/StreamCore32). Thanks to his efforts in figuring out the Qobuz Connect protocol, this project was possible.

## Disclaimer

This project was built almost entirely through agentic programming using [Claude Code](https://claude.ai/claude-code). The architecture, implementation, and tests were generated through AI-assisted development with human guidance and review.

## License

[MIT](LICENSE)
