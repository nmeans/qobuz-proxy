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
  -v ./data:/data \
  -e QOBUZPROXY_BACKEND=local \
  --device /dev/snd \
  ghcr.io/leolobato/qobuz-proxy:latest
```

Note: The `--device /dev/snd` flag gives the container access to the host's audio devices (Linux only). Qobuz credentials should be in your `data/config.yaml`.

## Installation

A pre-built Docker image is available from GitHub Container Registry:

```bash
docker pull ghcr.io/leolobato/qobuz-proxy:latest
```

### Quick Start (Docker, single speaker)

For a single DLNA speaker, you can use environment variables without a config file:

```bash
docker run -d --network host \
  -e QOBUZ_EMAIL=your@email.com \
  -e QOBUZ_PASSWORD=yourpassword \
  -e QOBUZPROXY_DLNA_IP=192.168.1.50 \
  -e QOBUZPROXY_DEVICE_NAME=Living\ Room \
  -v ./data:/data \
  ghcr.io/leolobato/qobuz-proxy:latest
```

The `/data` volume is optional here but recommended to persist the credential cache across restarts.

### Quick Start (Docker, multiple speakers)

For multiple speakers, create a `config.yaml` in your data directory:

```bash
mkdir data
cp config.yaml.example data/config.yaml
nano data/config.yaml  # Edit with your values
```

Then run with Docker Compose:
```bash
docker-compose up -d
```

Or run directly:
```bash
docker run -d --network host -v ./data:/data ghcr.io/leolobato/qobuz-proxy:latest
```

View logs:
```bash
docker-compose logs -f
```

### Quick Start (without Docker)

```bash
pip install .
cp config.yaml.example config.yaml  # Edit with your values
qobuz-proxy
```

QobuzProxy looks for `config.yaml` in the current directory by default. Use `--config` to specify a different path.

### Multi-Speaker Setup

A single QobuzProxy instance can manage multiple speakers. Each speaker appears as a separate device in the Qobuz app. Use the `speakers` key in your config file:

```yaml
qobuz:
  email: "user@example.com"
  password: "password"

speakers:
  - name: "Living Room"
    backend: dlna
    dlna_ip: "192.168.1.50"
    max_quality: auto

  - name: "Office"
    backend: dlna
    dlna_ip: "192.168.1.51"
    max_quality: 7

  - name: "Headphones"
    backend: local
    audio_device: "Built-in Output"
```

Ports are auto-assigned unless explicitly set via `http_port` and `proxy_port`. See `config.yaml.example` for all available options.

### Network Requirements

**Important**: QobuzProxy requires `network_mode: host` (Docker) or direct host access for mDNS discovery to work. This allows the Qobuz app to find the device on your local network.

If you cannot use host networking, consider:
- Using a macvlan network with a dedicated IP on your LAN
- Running QobuzProxy directly on the host (not in Docker)

### Configuration

The config file is found automatically in this order:

1. `--config` CLI argument (if provided)
2. `./config.yaml` (current directory)
3. `$QOBUZPROXY_DATA_DIR/config.yaml` (set to `/data` in the Docker image)

Environment variables and CLI arguments override config file values. See `.env.example` for available environment variables.

### Data Directory

In Docker, both the config file and credential cache live under `/data`:

```yaml
volumes:
  - ./data:/data
```

The credential cache stores Qobuz web player credentials so they don't need to be re-scraped on each restart. Outside Docker, the cache defaults to `~/.qobuz-proxy/`.

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
