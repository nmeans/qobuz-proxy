# Multi-Speaker Support

## Overview

Allow a single QobuzProxy process to expose multiple independent Qobuz Connect speakers, each mapped to its own audio backend (DLNA renderer or local audio device). Each speaker appears as a separate device in the Qobuz app with its own queue and playback controls.

## Architecture

### Speaker Container Pattern

Introduce a `Speaker` class that bundles all per-speaker components. `QobuzProxy` becomes a thin orchestrator holding shared resources and a list of `Speaker` instances.

```
QobuzProxy (orchestrator)
├── QobuzAPIClient (shared)
├── MetadataService (shared, keyed by track_id + quality)
├── Speaker "Living Room"
│   ├── DiscoveryService (port 8689)
│   ├── WsManager (unique WebSocket + device UUID)
│   ├── AudioProxyServer (port 7120, DLNA only)
│   ├── Player, Queue, Handlers, StateReporter
│   └── DLNABackend (192.168.1.50)
├── Speaker "Office"
│   ├── DiscoveryService (port 8690)
│   ├── WsManager (unique WebSocket + device UUID)
│   ├── AudioProxyServer (port 7121)
│   ├── Player, Queue, Handlers, StateReporter
│   └── DLNABackend (192.168.1.51)
```

### Shared vs Per-Speaker Components

| Component | Scope | Notes |
|-----------|-------|-------|
| QobuzAPIClient | Shared | Same Qobuz account for all speakers |
| MetadataService | Shared | Cache keyed by (track_id, quality) |
| Protocol (codec) | Shared | Stateless encoder/decoder |
| App credentials | Shared | Same app_id/app_secret |
| DiscoveryService | Per-speaker | Unique mDNS name + HTTP port |
| WsManager | Per-speaker | Unique WebSocket connection + device UUID |
| AudioProxyServer | Per-speaker | Unique port, DLNA speakers only |
| Player | Per-speaker | Independent state machine |
| Queue | Per-speaker | Independent queue/shuffle/repeat |
| AudioBackend | Per-speaker | Connected to specific device |
| Handlers | Per-speaker | Route commands to their speaker's player |
| StateReporter | Per-speaker | Reports to their speaker's WsManager |

## Configuration

### YAML Format

```yaml
email: user@example.com
password: secret

speakers:
  - name: "Living Room"
    backend: dlna
    dlna_ip: 192.168.1.50
    max_quality: auto
    # http_port: 8689      # optional, auto-assigned
    # proxy_port: 7120     # optional, auto-assigned
    # uuid: "..."          # optional, auto-generated

  - name: "Office"
    backend: dlna
    dlna_ip: 192.168.1.51
    max_quality: 7

  - name: "Headphones"
    backend: local
    audio_device: "Built-in Output"
```

### Environment Variables

Single speaker (backwards-compatible, unchanged):

```bash
QOBUZPROXY_DEVICE_NAME=Living Room
QOBUZPROXY_BACKEND=dlna
QOBUZPROXY_DLNA_IP=192.168.1.50
```

Multiple speakers (comma-separated):

```bash
QOBUZPROXY_DEVICE_NAME=Living Room,Office
QOBUZPROXY_BACKEND=dlna,dlna
QOBUZPROXY_DLNA_IP=192.168.1.50,192.168.1.51
QOBUZPROXY_MAX_QUALITY=auto,7
```

### Config Normalization

All config sources (YAML, env vars, CLI) normalize into a list of `SpeakerConfig` dataclasses at parse time. A flat/single config becomes a one-element list. Validation checks:

- Comma-separated env vars have matching lengths across all speaker-related vars
- No duplicate device names
- No port conflicts (between explicit assignments)

### Port Assignment

Auto-incrementing from defaults: first speaker gets 8689 (HTTP) / 7120 (proxy), second gets 8690/7121, etc. Explicitly set ports are respected and skipped during auto-assignment.

### Device UUIDs

Each speaker needs a stable UUID for Qobuz Connect identity. Generated deterministically from a hash of the device name + a machine-specific seed. Survives restarts without explicit config. Overridable in YAML.

## Speaker Class & Lifecycle

### Speaker

```python
class Speaker:
    def __init__(self, config: SpeakerConfig, api_client: QobuzAPIClient,
                 metadata_service: MetadataService):
        self.config = config
        self.api_client = api_client
        self.metadata_service = metadata_service

    async def start(self) -> None:
        # Creates and wires: DiscoveryService, WsManager,
        # AudioBackend, AudioProxyServer (if DLNA), Player,
        # Queue, Handlers, StateReporter
        # Registers mDNS, starts HTTP discovery, connects WebSocket

    async def stop(self) -> None:
        # Graceful teardown in reverse order
        # Deregisters mDNS, closes WebSocket, stops backend
```

### QobuzProxy (orchestrator)

```python
class QobuzProxy:
    async def start(self) -> None:
        # 1. Parse config -> list of SpeakerConfig
        # 2. Scrape app credentials (once)
        # 3. Create QobuzAPIClient (once)
        # 4. Login (once)
        # 5. Create MetadataService (once)
        # 6. For each SpeakerConfig -> create Speaker
        # 7. Start all speakers concurrently (asyncio.gather)
        #    Failed speakers log warning, others continue

    async def stop(self) -> None:
        # Stop all speakers concurrently
```

## Error Handling

### Startup Failures

If a speaker fails to start (DLNA device unreachable, port conflict), it logs a warning and the remaining speakers continue. The process only exits if zero speakers start successfully.

### Runtime Failures

Each speaker's WebSocket reconnect and backend error handling is self-contained. A failure in one speaker does not propagate to others. Existing reconnect logic in WsManager and backend polling continues to work per-speaker.

## Changes to Existing Components

### No Code Changes

- `Player`, `Queue`, `QueueHandler`, `PlaybackCommandHandler`, `VolumeCommandHandler` — already operate on injected dependencies
- `Protocol` — stateless encoder/decoder
- `AudioBackend` implementations (DLNA, Local) — already parameterized
- `StateReporter` — already takes player and ws_manager references

### Minor Changes (parameterization)

- `DiscoveryService` — accept HTTP port from `SpeakerConfig` instead of global config
- `WsManager` — accept device UUID from `SpeakerConfig`
- `AudioProxyServer` — accept proxy port from `SpeakerConfig`
- `MetadataService` — verify cache key includes quality level

### Moderate Changes

- `app.py` — refactor `QobuzProxy.start()`: shared setup stays, per-speaker setup moves to `Speaker`
- Config parsing — new `SpeakerConfig` dataclass, normalization logic, comma-separated env var parsing, port auto-assignment
- No new CLI flags required

## Docker & Networking

`docker-compose.yaml` uses `network_mode: host`, so auto-assigned ports are directly accessible without mapping changes. `.env.example` and `config.yaml.example` updated with multi-speaker examples.

Each speaker registers its own mDNS service. Multiple speakers on one host appear as separate devices in the Qobuz app.

## Backwards Compatibility

A config with no `speakers:` key and no comma-separated env vars works exactly as today — parsed into a one-element speaker list with the same ports and behavior. Zero user-facing changes for existing single-speaker setups.

## Testing Strategy

### Existing Tests (unchanged)

All 141 existing tests remain untouched since component internals don't change.

### New Unit Tests

- `SpeakerConfig` parsing: flat config to one-element list, YAML speakers list, comma-separated env vars, mismatched lengths error, port auto-assignment, UUID generation determinism
- `Speaker` lifecycle: start/stop, component wiring, error during start doesn't raise

### New Integration Tests

- `QobuzProxy` with multiple `SpeakerConfig` entries: unique ports, shared API client, independent start/stop
- Failure isolation: one speaker's backend failing doesn't affect others
- Backwards compatibility: existing single-speaker config produces identical behavior

## Out of Scope

- Grouped/synced playback across speakers
- Config generation from `--discover`
- Dynamic speaker addition/removal at runtime
- Per-speaker authentication (different Qobuz accounts)
