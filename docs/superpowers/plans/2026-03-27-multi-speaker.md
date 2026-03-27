# Multi-Speaker Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow a single QobuzProxy process to expose multiple independent Qobuz Connect speakers, each mapped to its own audio backend.

**Architecture:** Introduce a `SpeakerConfig` dataclass and `Speaker` class. `Speaker` bundles all per-speaker components (discovery, WebSocket, backend, player, queue). `QobuzProxy` becomes a thin orchestrator that creates shared resources (API client) and a list of `Speaker` instances. Existing components receive a synthesized per-speaker `Config` object so they require zero internal changes.

**Tech Stack:** Python 3.10+, asyncio, dataclasses, pytest, uuid5

**Design spec:** `docs/superpowers/specs/2026-03-27-multi-speaker-design.md`

---

## File Structure

### New Files
- `qobuz_proxy/speaker.py` — `Speaker` class that bundles per-speaker components and lifecycle
- `tests/test_speaker_config.py` — Tests for `SpeakerConfig` parsing, normalization, port assignment, UUID generation
- `tests/test_speaker.py` — Tests for `Speaker` class lifecycle
- `tests/test_multi_speaker.py` — Integration tests for multi-speaker `QobuzProxy`

### Modified Files
- `qobuz_proxy/config.py` — Add `SpeakerConfig` dataclass, `build_speaker_configs()`, port assignment, UUID generation, update `load_config()` to extract YAML `speakers:` key and populate `Config.speakers`
- `qobuz_proxy/app.py` — Refactor `QobuzProxy` to iterate over `config.speakers`, create `Speaker` instances, handle concurrent start/stop with error isolation
- `qobuz_proxy/cli.py` — Update `log_config()` to show per-speaker info
- `.env.example` — Add multi-speaker env var examples
- `config.yaml.example` — Add `speakers:` list example

### Unchanged Files
- All existing component files (`discovery.py`, `ws_manager.py`, `proxy_server.py`, `player.py`, `queue.py`, handlers, `metadata.py`, `protocol.py`, backends)
- All existing test files

---

## Task 1: SpeakerConfig and Config Normalization

**Files:**
- Modify: `qobuz_proxy/config.py`
- Create: `tests/test_speaker_config.py`

### Step 1.1: Write failing test for SpeakerConfig defaults

- [ ] Create the test file:

```python
# tests/test_speaker_config.py
"""Tests for SpeakerConfig and multi-speaker config normalization."""

from qobuz_proxy.config import SpeakerConfig


class TestSpeakerConfigDefaults:
    def test_default_values(self):
        sc = SpeakerConfig()
        assert sc.name == "QobuzProxy"
        assert sc.uuid == ""
        assert sc.backend_type == "dlna"
        assert sc.max_quality == 27
        assert sc.http_port == 0
        assert sc.bind_address == "0.0.0.0"
        assert sc.dlna_ip == ""
        assert sc.dlna_port == 1400
        assert sc.dlna_fixed_volume is False
        assert sc.proxy_port == 0
        assert sc.audio_device == "default"
        assert sc.audio_buffer_size == 2048

    def test_custom_values(self):
        sc = SpeakerConfig(
            name="Living Room",
            backend_type="dlna",
            dlna_ip="192.168.1.50",
            max_quality=7,
            http_port=8700,
            proxy_port=7200,
        )
        assert sc.name == "Living Room"
        assert sc.dlna_ip == "192.168.1.50"
        assert sc.max_quality == 7
        assert sc.http_port == 8700
        assert sc.proxy_port == 7200
```

- [ ] Run test to verify it fails:

Run: `cd /Users/leolobato/Documents/Projetos/Personal/Qobuz/qobuz-proxy/.worktrees/multi-speaker && source venv/bin/activate && pytest tests/test_speaker_config.py -v`
Expected: FAIL with `ImportError: cannot import name 'SpeakerConfig' from 'qobuz_proxy.config'`

### Step 1.2: Implement SpeakerConfig dataclass

- [ ] Add to `qobuz_proxy/config.py` after the `Config` class (after line 134):

```python
@dataclass
class SpeakerConfig:
    """Per-speaker configuration."""

    name: str = "QobuzProxy"
    uuid: str = ""  # Auto-generated if empty
    backend_type: str = "dlna"
    max_quality: int = 27
    http_port: int = 0  # 0 = auto-assign
    bind_address: str = "0.0.0.0"
    # DLNA
    dlna_ip: str = ""
    dlna_port: int = 1400
    dlna_fixed_volume: bool = False
    proxy_port: int = 0  # 0 = auto-assign
    # Local
    audio_device: str = "default"
    audio_buffer_size: int = 2048
```

Also add a `speakers` field to the `Config` dataclass (line ~134):

```python
@dataclass
class Config:
    """Complete QobuzProxy configuration."""

    qobuz: QobuzConfig = field(default_factory=QobuzConfig)
    device: DeviceConfig = field(default_factory=DeviceConfig)
    backend: BackendConfig = field(default_factory=BackendConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    speakers: list["SpeakerConfig"] = field(default_factory=list)
```

- [ ] Run test to verify it passes:

Run: `cd /Users/leolobato/Documents/Projetos/Personal/Qobuz/qobuz-proxy/.worktrees/multi-speaker && source venv/bin/activate && pytest tests/test_speaker_config.py -v`
Expected: PASS

### Step 1.3: Write failing test for single-speaker normalization

- [ ] Add to `tests/test_speaker_config.py`:

```python
from qobuz_proxy.config import (
    Config,
    QobuzConfig,
    DeviceConfig,
    BackendConfig,
    DLNAConfig,
    LocalConfig,
    ServerConfig,
    SpeakerConfig,
    build_speaker_configs,
)


class TestSingleSpeakerNormalization:
    def test_flat_config_produces_one_speaker(self):
        config = Config(
            device=DeviceConfig(name="MyDevice", uuid="test-uuid-1234"),
            backend=BackendConfig(
                type="dlna",
                dlna=DLNAConfig(ip="192.168.1.50", port=1400, proxy_port=7120),
            ),
            server=ServerConfig(http_port=8689),
        )
        speakers = build_speaker_configs(config)
        assert len(speakers) == 1
        s = speakers[0]
        assert s.name == "MyDevice"
        assert s.uuid == "test-uuid-1234"
        assert s.backend_type == "dlna"
        assert s.dlna_ip == "192.168.1.50"
        assert s.http_port == 8689
        assert s.proxy_port == 7120

    def test_flat_local_backend(self):
        config = Config(
            device=DeviceConfig(name="Headphones"),
            backend=BackendConfig(
                type="local",
                local=LocalConfig(device="Built-in Output"),
            ),
            server=ServerConfig(http_port=8689),
        )
        speakers = build_speaker_configs(config)
        assert len(speakers) == 1
        s = speakers[0]
        assert s.name == "Headphones"
        assert s.backend_type == "local"
        assert s.audio_device == "Built-in Output"
        assert s.http_port == 8689
```

- [ ] Run test to verify it fails:

Run: `pytest tests/test_speaker_config.py::TestSingleSpeakerNormalization -v`
Expected: FAIL with `ImportError: cannot import name 'build_speaker_configs'`

### Step 1.4: Implement build_speaker_configs and single-speaker path

- [ ] Add to `qobuz_proxy/config.py` after the `SpeakerConfig` class:

```python
import platform


# Fixed namespace for deterministic UUID generation
_SPEAKER_UUID_NAMESPACE = uuid.UUID("f47ac10b-58cc-4372-a567-0e02b2c3d479")

DEFAULT_HTTP_PORT = 8689
DEFAULT_PROXY_PORT = 7120


def generate_speaker_uuid(speaker_name: str) -> str:
    """Generate a deterministic UUID for a speaker based on name and machine."""
    machine_id = platform.node()
    return str(uuid.uuid5(_SPEAKER_UUID_NAMESPACE, f"{machine_id}:{speaker_name}"))


def _single_speaker_from_config(config: Config) -> SpeakerConfig:
    """Build a single SpeakerConfig from flat Config fields."""
    return SpeakerConfig(
        name=config.device.name,
        uuid=config.device.uuid,
        backend_type=config.backend.type,
        max_quality=config.qobuz.max_quality,
        http_port=config.server.http_port,
        bind_address=config.server.bind_address,
        dlna_ip=config.backend.dlna.ip,
        dlna_port=config.backend.dlna.port,
        dlna_fixed_volume=config.backend.dlna.fixed_volume,
        proxy_port=config.backend.dlna.proxy_port,
        audio_device=config.backend.local.device,
        audio_buffer_size=config.backend.local.buffer_size,
    )


def _assign_ports(speakers: list[SpeakerConfig]) -> None:
    """Auto-assign ports to speakers that don't specify them."""
    used_http = {s.http_port for s in speakers if s.http_port > 0}
    used_proxy = {s.proxy_port for s in speakers if s.proxy_port > 0}

    next_http = DEFAULT_HTTP_PORT
    next_proxy = DEFAULT_PROXY_PORT

    for speaker in speakers:
        if speaker.http_port == 0:
            while next_http in used_http:
                next_http += 1
            speaker.http_port = next_http
            used_http.add(next_http)
            next_http += 1

        if speaker.proxy_port == 0 and speaker.backend_type == "dlna":
            while next_proxy in used_proxy:
                next_proxy += 1
            speaker.proxy_port = next_proxy
            used_proxy.add(next_proxy)
            next_proxy += 1


def _generate_uuids(speakers: list[SpeakerConfig]) -> None:
    """Generate UUIDs for speakers that don't have one."""
    for speaker in speakers:
        if not speaker.uuid:
            speaker.uuid = generate_speaker_uuid(speaker.name)


def _validate_speakers(speakers: list[SpeakerConfig]) -> None:
    """Validate speaker configs for conflicts."""
    if not speakers:
        raise ConfigError("At least one speaker must be configured")

    names = [s.name for s in speakers]
    if len(names) != len(set(names)):
        dupes = {n for n in names if names.count(n) > 1}
        raise ConfigError(f"Duplicate speaker names: {dupes}")

    http_ports = [s.http_port for s in speakers if s.http_port > 0]
    if len(http_ports) != len(set(http_ports)):
        raise ConfigError("HTTP port conflict between speakers")

    proxy_ports = [s.proxy_port for s in speakers if s.proxy_port > 0 and s.backend_type == "dlna"]
    if len(proxy_ports) != len(set(proxy_ports)):
        raise ConfigError("Proxy port conflict between speakers")


def build_speaker_configs(
    config: Config,
    raw_yaml_speakers: Optional[list[dict[str, Any]]] = None,
) -> list[SpeakerConfig]:
    """
    Build speaker configs from the global config.

    Priority:
    1. YAML speakers list (if provided)
    2. Comma-separated env vars (if QOBUZPROXY_DEVICE_NAME contains comma)
    3. Single speaker from flat config
    """
    if raw_yaml_speakers:
        speakers = _parse_yaml_speakers(raw_yaml_speakers, config)
    elif "," in os.environ.get("QOBUZPROXY_DEVICE_NAME", ""):
        speakers = _parse_env_speakers(config)
    else:
        speakers = [_single_speaker_from_config(config)]

    _assign_ports(speakers)
    _generate_uuids(speakers)
    _validate_speakers(speakers)
    return speakers
```

- [ ] Run test to verify it passes:

Run: `pytest tests/test_speaker_config.py -v`
Expected: PASS

### Step 1.5: Write failing test for YAML speakers parsing

- [ ] Add to `tests/test_speaker_config.py`:

```python
from qobuz_proxy.config import AUTO_QUALITY


class TestYAMLSpeakersParsing:
    def test_two_dlna_speakers(self):
        config = Config()
        raw = [
            {"name": "Living Room", "backend": "dlna", "dlna_ip": "192.168.1.50", "max_quality": 7},
            {"name": "Office", "backend": "dlna", "dlna_ip": "192.168.1.51"},
        ]
        speakers = build_speaker_configs(config, raw_yaml_speakers=raw)
        assert len(speakers) == 2
        assert speakers[0].name == "Living Room"
        assert speakers[0].dlna_ip == "192.168.1.50"
        assert speakers[0].max_quality == 7
        assert speakers[1].name == "Office"
        assert speakers[1].dlna_ip == "192.168.1.51"
        assert speakers[1].max_quality == 27  # default

    def test_auto_quality_string(self):
        config = Config()
        raw = [{"name": "Test", "backend": "dlna", "dlna_ip": "1.2.3.4", "max_quality": "auto"}]
        speakers = build_speaker_configs(config, raw_yaml_speakers=raw)
        assert speakers[0].max_quality == AUTO_QUALITY

    def test_mixed_backends(self):
        config = Config()
        raw = [
            {"name": "Sonos", "backend": "dlna", "dlna_ip": "192.168.1.50"},
            {"name": "Headphones", "backend": "local", "audio_device": "Built-in Output"},
        ]
        speakers = build_speaker_configs(config, raw_yaml_speakers=raw)
        assert speakers[0].backend_type == "dlna"
        assert speakers[1].backend_type == "local"
        assert speakers[1].audio_device == "Built-in Output"

    def test_explicit_ports_respected(self):
        config = Config()
        raw = [
            {"name": "A", "backend": "dlna", "dlna_ip": "1.2.3.4", "http_port": 9000, "proxy_port": 9100},
            {"name": "B", "backend": "dlna", "dlna_ip": "1.2.3.5"},
        ]
        speakers = build_speaker_configs(config, raw_yaml_speakers=raw)
        assert speakers[0].http_port == 9000
        assert speakers[0].proxy_port == 9100
        assert speakers[1].http_port == 8689  # auto-assigned, skips 9000
        assert speakers[1].proxy_port == 7120  # auto-assigned, skips 9100

    def test_explicit_uuid_preserved(self):
        config = Config()
        raw = [{"name": "Test", "backend": "dlna", "dlna_ip": "1.2.3.4", "uuid": "my-custom-uuid"}]
        speakers = build_speaker_configs(config, raw_yaml_speakers=raw)
        assert speakers[0].uuid == "my-custom-uuid"
```

- [ ] Run test to verify it fails:

Run: `pytest tests/test_speaker_config.py::TestYAMLSpeakersParsing -v`
Expected: FAIL with `NameError: name '_parse_yaml_speakers' is not defined`

### Step 1.6: Implement YAML speakers parsing

- [ ] Add to `qobuz_proxy/config.py`:

```python
def _parse_quality_value(value: Any) -> int:
    """Parse a quality value from YAML or env, handling 'auto' string."""
    if isinstance(value, str) and value.lower() == "auto":
        return AUTO_QUALITY
    return int(value)


def _parse_yaml_speakers(
    raw_speakers: list[dict[str, Any]], config: Config
) -> list[SpeakerConfig]:
    """Parse speakers from YAML speakers list."""
    speakers = []
    for data in raw_speakers:
        speakers.append(
            SpeakerConfig(
                name=data.get("name", "QobuzProxy"),
                uuid=data.get("uuid", ""),
                backend_type=data.get("backend", "dlna"),
                max_quality=_parse_quality_value(data.get("max_quality", 27)),
                http_port=data.get("http_port", 0),
                bind_address=data.get("bind_address", config.server.bind_address),
                dlna_ip=data.get("dlna_ip", ""),
                dlna_port=data.get("dlna_port", 1400),
                dlna_fixed_volume=data.get("dlna_fixed_volume", False),
                proxy_port=data.get("proxy_port", 0),
                audio_device=data.get("audio_device", "default"),
                audio_buffer_size=data.get("audio_buffer_size", 2048),
            )
        )
    return speakers
```

- [ ] Run test to verify it passes:

Run: `pytest tests/test_speaker_config.py -v`
Expected: PASS

### Step 1.7: Write failing test for comma-separated env vars

- [ ] Add to `tests/test_speaker_config.py`:

```python
class TestEnvVarSpeakersParsing:
    def test_comma_separated_names(self, monkeypatch):
        monkeypatch.setenv("QOBUZPROXY_DEVICE_NAME", "Living Room,Office")
        monkeypatch.setenv("QOBUZPROXY_BACKEND", "dlna,dlna")
        monkeypatch.setenv("QOBUZPROXY_DLNA_IP", "192.168.1.50,192.168.1.51")
        config = Config()
        speakers = build_speaker_configs(config)
        assert len(speakers) == 2
        assert speakers[0].name == "Living Room"
        assert speakers[0].dlna_ip == "192.168.1.50"
        assert speakers[1].name == "Office"
        assert speakers[1].dlna_ip == "192.168.1.51"

    def test_single_backend_broadcasts(self, monkeypatch):
        """When backend has no comma, it applies to all speakers."""
        monkeypatch.setenv("QOBUZPROXY_DEVICE_NAME", "A,B")
        monkeypatch.setenv("QOBUZPROXY_BACKEND", "dlna")
        monkeypatch.setenv("QOBUZPROXY_DLNA_IP", "1.2.3.4,1.2.3.5")
        config = Config()
        speakers = build_speaker_configs(config)
        assert speakers[0].backend_type == "dlna"
        assert speakers[1].backend_type == "dlna"

    def test_quality_with_auto(self, monkeypatch):
        monkeypatch.setenv("QOBUZPROXY_DEVICE_NAME", "A,B")
        monkeypatch.setenv("QOBUZPROXY_DLNA_IP", "1.2.3.4,1.2.3.5")
        monkeypatch.setenv("QOBUZ_MAX_QUALITY", "auto,7")
        config = Config()
        speakers = build_speaker_configs(config)
        assert speakers[0].max_quality == AUTO_QUALITY
        assert speakers[1].max_quality == 7

    def test_mismatched_lengths_raises(self, monkeypatch):
        monkeypatch.setenv("QOBUZPROXY_DEVICE_NAME", "A,B,C")
        monkeypatch.setenv("QOBUZPROXY_DLNA_IP", "1.2.3.4")  # only 1, expect 3
        config = Config()
        import pytest
        with pytest.raises(ConfigError):
            build_speaker_configs(config)
```

- [ ] Run test to verify it fails:

Run: `pytest tests/test_speaker_config.py::TestEnvVarSpeakersParsing -v`
Expected: FAIL with `NameError: name '_parse_env_speakers' is not defined`

### Step 1.8: Implement comma-separated env var parsing

- [ ] Add to `qobuz_proxy/config.py`:

```python
def _split_env(var: str) -> list[str]:
    """Split a comma-separated env var. Returns empty list if not set."""
    value = os.environ.get(var, "")
    if not value:
        return []
    return [v.strip() for v in value.split(",")]


def _split_env_padded(var: str, count: int, default: str) -> list[str]:
    """Split env var and pad/broadcast to count elements.

    If the env var has 1 value and count > 1, broadcast to all.
    If it has count values, use as-is.
    Otherwise raise ConfigError.
    """
    values = _split_env(var)
    if not values:
        return [default] * count
    if len(values) == 1:
        return values * count
    if len(values) != count:
        raise ConfigError(
            f"Environment variable {var} has {len(values)} values, "
            f"expected 1 or {count} (matching QOBUZPROXY_DEVICE_NAME)"
        )
    return values


def _parse_env_speakers(config: Config) -> list[SpeakerConfig]:
    """Parse multiple speakers from comma-separated env vars."""
    names = _split_env("QOBUZPROXY_DEVICE_NAME")
    count = len(names)

    backends = _split_env_padded("QOBUZPROXY_BACKEND", count, "dlna")
    dlna_ips = _split_env_padded("QOBUZPROXY_DLNA_IP", count, "")
    dlna_ports = _split_env_padded("QOBUZPROXY_DLNA_PORT", count, "1400")
    qualities = _split_env_padded("QOBUZ_MAX_QUALITY", count, "27")
    http_ports = _split_env_padded("QOBUZPROXY_HTTP_PORT", count, "0")
    proxy_ports = _split_env_padded("QOBUZPROXY_PROXY_PORT", count, "0")
    audio_devices = _split_env_padded("QOBUZPROXY_AUDIO_DEVICE", count, "default")
    fixed_volumes = _split_env_padded("QOBUZPROXY_DLNA_FIXED_VOLUME", count, "false")

    speakers = []
    for i in range(count):
        speakers.append(
            SpeakerConfig(
                name=names[i],
                backend_type=backends[i],
                max_quality=_parse_quality_value(qualities[i]),
                dlna_ip=dlna_ips[i],
                dlna_port=int(dlna_ports[i]),
                dlna_fixed_volume=fixed_volumes[i].lower() in ("true", "1", "yes", "on"),
                http_port=int(http_ports[i]),
                proxy_port=int(proxy_ports[i]),
                audio_device=audio_devices[i],
                bind_address=config.server.bind_address,
            )
        )
    return speakers
```

- [ ] Run test to verify it passes:

Run: `pytest tests/test_speaker_config.py -v`
Expected: PASS

### Step 1.9: Write failing test for port assignment and UUID generation

- [ ] Add to `tests/test_speaker_config.py`:

```python
from qobuz_proxy.config import generate_speaker_uuid, _assign_ports


class TestPortAssignment:
    def test_auto_assign_from_defaults(self):
        speakers = [
            SpeakerConfig(name="A", backend_type="dlna", dlna_ip="1.2.3.4"),
            SpeakerConfig(name="B", backend_type="dlna", dlna_ip="1.2.3.5"),
            SpeakerConfig(name="C", backend_type="dlna", dlna_ip="1.2.3.6"),
        ]
        _assign_ports(speakers)
        assert speakers[0].http_port == 8689
        assert speakers[0].proxy_port == 7120
        assert speakers[1].http_port == 8690
        assert speakers[1].proxy_port == 7121
        assert speakers[2].http_port == 8691
        assert speakers[2].proxy_port == 7122

    def test_skip_explicit_ports(self):
        speakers = [
            SpeakerConfig(name="A", backend_type="dlna", dlna_ip="1.2.3.4", http_port=8689),
            SpeakerConfig(name="B", backend_type="dlna", dlna_ip="1.2.3.5"),
        ]
        _assign_ports(speakers)
        assert speakers[0].http_port == 8689  # explicit
        assert speakers[1].http_port == 8690  # auto, next after 8689

    def test_local_backend_no_proxy_port(self):
        speakers = [
            SpeakerConfig(name="A", backend_type="local"),
        ]
        _assign_ports(speakers)
        assert speakers[0].http_port == 8689
        assert speakers[0].proxy_port == 0  # not assigned for local


class TestUUIDGeneration:
    def test_deterministic(self):
        uuid1 = generate_speaker_uuid("Living Room")
        uuid2 = generate_speaker_uuid("Living Room")
        assert uuid1 == uuid2

    def test_different_names_different_uuids(self):
        uuid1 = generate_speaker_uuid("Living Room")
        uuid2 = generate_speaker_uuid("Office")
        assert uuid1 != uuid2

    def test_valid_uuid_format(self):
        result = generate_speaker_uuid("Test")
        import uuid as uuid_mod
        uuid_mod.UUID(result)  # Should not raise
```

- [ ] Run test to verify it passes (implementation from step 1.4 covers this):

Run: `pytest tests/test_speaker_config.py -v`
Expected: PASS

### Step 1.10: Write failing test for validation

- [ ] Add to `tests/test_speaker_config.py`:

```python
import pytest
from qobuz_proxy.config import ConfigError, _validate_speakers


class TestSpeakerValidation:
    def test_duplicate_names_rejected(self):
        speakers = [
            SpeakerConfig(name="Same", http_port=8689),
            SpeakerConfig(name="Same", http_port=8690),
        ]
        with pytest.raises(ConfigError, match="Duplicate speaker names"):
            _validate_speakers(speakers)

    def test_http_port_conflict_rejected(self):
        speakers = [
            SpeakerConfig(name="A", http_port=8689),
            SpeakerConfig(name="B", http_port=8689),
        ]
        with pytest.raises(ConfigError, match="HTTP port conflict"):
            _validate_speakers(speakers)

    def test_empty_speakers_rejected(self):
        with pytest.raises(ConfigError, match="At least one speaker"):
            _validate_speakers([])

    def test_valid_speakers_pass(self):
        speakers = [
            SpeakerConfig(name="A", http_port=8689),
            SpeakerConfig(name="B", http_port=8690),
        ]
        _validate_speakers(speakers)  # Should not raise
```

- [ ] Run test to verify it passes:

Run: `pytest tests/test_speaker_config.py -v`
Expected: PASS

### Step 1.11: Update load_config to handle YAML speakers key

- [ ] Add test for YAML speakers extraction in `tests/test_speaker_config.py`:

```python
from pathlib import Path
import tempfile
from qobuz_proxy.config import load_config


class TestLoadConfigSpeakers:
    def test_yaml_speakers_key_extracted(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
qobuz:
  email: "test@example.com"
  password: "secret"
speakers:
  - name: "Living Room"
    backend: dlna
    dlna_ip: "192.168.1.50"
  - name: "Office"
    backend: dlna
    dlna_ip: "192.168.1.51"
"""
        )
        config = load_config(config_path=config_file)
        assert len(config.speakers) == 2
        assert config.speakers[0].name == "Living Room"
        assert config.speakers[1].name == "Office"

    def test_flat_config_produces_one_speaker(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
qobuz:
  email: "test@example.com"
  password: "secret"
device:
  name: "MyProxy"
backend:
  type: dlna
  dlna:
    ip: "192.168.1.50"
"""
        )
        config = load_config(config_path=config_file)
        assert len(config.speakers) == 1
        assert config.speakers[0].name == "MyProxy"
        assert config.speakers[0].dlna_ip == "192.168.1.50"
```

- [ ] Modify `load_config()` in `qobuz_proxy/config.py` to extract `speakers:` from YAML and populate `config.speakers`:

Replace the `load_config` function body (lines 359-412) with:

```python
def load_config(
    config_path: Optional[Path] = None,
    cli_args: Optional[dict] = None,
) -> Config:
    """
    Load configuration from all sources.

    Priority (highest to lowest):
    1. CLI arguments
    2. Environment variables
    3. Config file
    4. Defaults

    Args:
        config_path: Path to YAML config file
        cli_args: Dictionary of CLI arguments

    Returns:
        Merged Config object

    Raises:
        ConfigError: If configuration is invalid
    """
    configs = []
    raw_yaml_speakers: Optional[list[dict[str, Any]]] = None

    # 1. Load from file (lowest priority of explicit configs)
    if config_path:
        file_config = load_yaml_config(config_path)
        if file_config:
            # Extract speakers before merging (not part of flat Config)
            raw_yaml_speakers = file_config.pop("speakers", None)
            configs.append(file_config)
            logger.debug(f"Loaded config from {config_path}")

    # 2. Load from environment
    env_config = load_env_config()
    if env_config:
        configs.append(env_config)
        logger.debug("Loaded config from environment variables")

    # 3. Load from CLI (highest priority)
    if cli_args:
        configs.append(cli_args)
        logger.debug("Loaded config from CLI arguments")

    # Merge all configs
    merged = merge_configs(*configs) if configs else {}

    # Convert to Config object (fills in defaults)
    config = dict_to_config(merged)

    # Build speaker configs
    config.speakers = build_speaker_configs(config, raw_yaml_speakers)

    # Validate (skip per-speaker backend validation when using speakers list,
    # as individual speakers handle their own validation)
    if not raw_yaml_speakers and "," not in os.environ.get("QOBUZPROXY_DEVICE_NAME", ""):
        validate_config(config)

    return config
```

- [ ] Run all tests to verify nothing is broken:

Run: `pytest tests/test_speaker_config.py -v && pytest --ignore=tests/backends/test_local_backend.py --ignore=tests/backends/test_local_buffer_status.py --ignore=tests/backends/test_local_config.py --ignore=tests/backends/test_local_device.py --ignore=tests/backends/test_local_ring_buffer.py --ignore=tests/backends/test_local_seek.py --ignore=tests/test_app_local_backend.py -v`
Expected: ALL PASS

### Step 1.12: Commit

- [ ] Commit:

```bash
git add qobuz_proxy/config.py tests/test_speaker_config.py
git commit -m "feat(config): add SpeakerConfig and multi-speaker normalization

- Add SpeakerConfig dataclass with per-speaker fields
- Add build_speaker_configs() with three parsing paths:
  YAML speakers list, comma-separated env vars, flat config
- Deterministic UUID generation from speaker name + hostname
- Auto-incrementing port assignment (8689+, 7120+)
- Validation for duplicate names and port conflicts
- Config.speakers always populated by load_config()"
```

---

## Task 2: Speaker Class

**Files:**
- Create: `qobuz_proxy/speaker.py`
- Create: `tests/test_speaker.py`

### Step 2.1: Write failing test for Speaker construction and config synthesis

- [ ] Create `tests/test_speaker.py`:

```python
"""Tests for Speaker class."""

from unittest.mock import AsyncMock, MagicMock, patch

from qobuz_proxy.config import Config, SpeakerConfig
from qobuz_proxy.speaker import Speaker


class TestSpeakerConstruction:
    def test_creates_with_config(self):
        speaker_config = SpeakerConfig(
            name="Living Room",
            uuid="test-uuid",
            backend_type="dlna",
            dlna_ip="192.168.1.50",
            http_port=8689,
            proxy_port=7120,
            max_quality=7,
        )
        api_client = MagicMock()
        app_id = "test-app-id"

        speaker = Speaker(
            config=speaker_config,
            api_client=api_client,
            app_id=app_id,
        )
        assert speaker.config == speaker_config
        assert speaker.name == "Living Room"

    def test_build_component_config(self):
        speaker_config = SpeakerConfig(
            name="Living Room",
            uuid="test-uuid",
            backend_type="dlna",
            dlna_ip="192.168.1.50",
            dlna_port=1400,
            http_port=8689,
            proxy_port=7120,
            max_quality=7,
            bind_address="0.0.0.0",
        )
        api_client = MagicMock()

        speaker = Speaker(config=speaker_config, api_client=api_client, app_id="app-id")
        cc = speaker._build_component_config()

        assert cc.device.name == "Living Room"
        assert cc.device.uuid == "test-uuid"
        assert cc.backend.type == "dlna"
        assert cc.backend.dlna.ip == "192.168.1.50"
        assert cc.backend.dlna.port == 1400
        assert cc.backend.dlna.proxy_port == 7120
        assert cc.server.http_port == 8689
        assert cc.server.bind_address == "0.0.0.0"
        assert cc.qobuz.max_quality == 7
```

- [ ] Run test to verify it fails:

Run: `pytest tests/test_speaker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'qobuz_proxy.speaker'`

### Step 2.2: Implement Speaker class skeleton and config synthesis

- [ ] Create `qobuz_proxy/speaker.py`:

```python
"""
Speaker - bundles all per-speaker components and manages their lifecycle.

Each Speaker instance represents one Qobuz Connect device with its own
discovery service, WebSocket connection, player, queue, and audio backend.
"""

import asyncio
import logging
from typing import Optional

from qobuz_proxy.config import (
    AUTO_FALLBACK_QUALITY,
    AUTO_QUALITY,
    BackendConfig,
    Config,
    DeviceConfig,
    DLNAConfig,
    LocalConfig,
    QobuzConfig,
    ServerConfig,
    SpeakerConfig,
)
from qobuz_proxy.auth import QobuzAPIClient
from qobuz_proxy.connect import ConnectTokens, DiscoveryService, WsManager
from qobuz_proxy.playback import (
    MetadataService,
    PlaybackCommandHandler,
    QobuzPlayer,
    QobuzQueue,
    QueueHandler,
    StateReporter,
    VolumeCommandHandler,
)
from qobuz_proxy.backends import AudioBackend, BackendFactory, PlaybackState
from qobuz_proxy.backends.dlna import AudioProxyServer, DLNABackend, MetadataServiceURLProvider

logger = logging.getLogger(__name__)


class Speaker:
    """
    Bundles all per-speaker components and manages their lifecycle.

    Each Speaker appears as a separate Qobuz Connect device in the app.
    """

    def __init__(
        self,
        config: SpeakerConfig,
        api_client: QobuzAPIClient,
        app_id: str,
    ):
        self.config = config
        self._api_client = api_client
        self._app_id = app_id

        # Components (initialized in start())
        self._discovery: Optional[DiscoveryService] = None
        self._ws_manager: Optional[WsManager] = None
        self._metadata_service: Optional[MetadataService] = None
        self._queue: Optional[QobuzQueue] = None
        self._player: Optional[QobuzPlayer] = None
        self._backend: Optional[AudioBackend] = None
        self._proxy_server: Optional[AudioProxyServer] = None
        self._state_reporter: Optional[StateReporter] = None

        # Handlers
        self._queue_handler: Optional[QueueHandler] = None
        self._playback_handler: Optional[PlaybackCommandHandler] = None
        self._volume_handler: Optional[VolumeCommandHandler] = None

        # State
        self._effective_quality: int = config.max_quality
        self._is_running = False
        self._ws_connected_event = asyncio.Event()
        self._ws_setup_lock = asyncio.Lock()

    @property
    def name(self) -> str:
        """Speaker display name."""
        return self.config.name

    def _build_component_config(self) -> Config:
        """Build a Config object that existing components can consume."""
        return Config(
            qobuz=QobuzConfig(max_quality=self.config.max_quality),
            device=DeviceConfig(name=self.config.name, uuid=self.config.uuid),
            backend=BackendConfig(
                type=self.config.backend_type,
                dlna=DLNAConfig(
                    ip=self.config.dlna_ip,
                    port=self.config.dlna_port,
                    fixed_volume=self.config.dlna_fixed_volume,
                    proxy_port=self.config.proxy_port,
                ),
                local=LocalConfig(
                    device=self.config.audio_device,
                    buffer_size=self.config.audio_buffer_size,
                ),
            ),
            server=ServerConfig(
                http_port=self.config.http_port,
                bind_address=self.config.bind_address,
            ),
        )
```

- [ ] Run test to verify it passes:

Run: `pytest tests/test_speaker.py -v`
Expected: PASS

### Step 2.3: Write failing test for Speaker start/stop lifecycle

- [ ] Add to `tests/test_speaker.py`:

```python
class TestSpeakerLifecycle:
    async def test_start_creates_backend_and_discovery(self):
        speaker_config = SpeakerConfig(
            name="Test Speaker",
            uuid="test-uuid",
            backend_type="dlna",
            dlna_ip="192.168.1.50",
            http_port=8689,
            proxy_port=7120,
        )
        api_client = MagicMock()

        speaker = Speaker(config=speaker_config, api_client=api_client, app_id="app-id")

        mock_backend = AsyncMock(spec=AudioBackend)
        mock_backend.name = "MockBackend"
        mock_backend.get_recommended_quality = MagicMock(return_value=None)

        with (
            patch.object(BackendFactory, "create_from_config", return_value=mock_backend),
            patch.object(DiscoveryService, "start", new_callable=AsyncMock),
            patch.object(DiscoveryService, "__init__", return_value=None),
            patch.object(AudioProxyServer, "start", new_callable=AsyncMock),
            patch.object(AudioProxyServer, "__init__", return_value=None),
        ):
            await speaker.start()

        assert speaker._is_running
        assert speaker._backend is mock_backend
        assert speaker._player is not None
        assert speaker._queue is not None
        assert speaker._discovery is not None

    async def test_stop_tears_down_components(self):
        speaker_config = SpeakerConfig(
            name="Test Speaker",
            uuid="test-uuid",
            backend_type="dlna",
            dlna_ip="192.168.1.50",
            http_port=8689,
            proxy_port=7120,
        )
        api_client = MagicMock()

        speaker = Speaker(config=speaker_config, api_client=api_client, app_id="app-id")

        # Set up mock components as if started
        speaker._is_running = True
        speaker._discovery = AsyncMock()
        speaker._backend = AsyncMock()
        speaker._proxy_server = AsyncMock()
        speaker._player = AsyncMock()
        speaker._ws_manager = AsyncMock()
        speaker._state_reporter = AsyncMock()

        await speaker.stop()

        assert not speaker._is_running
        speaker._discovery.stop.assert_awaited_once()
        speaker._backend.disconnect.assert_awaited_once()

    async def test_start_failure_does_not_raise(self):
        """Speaker.start() should not raise - failures are logged."""
        speaker_config = SpeakerConfig(
            name="Bad Speaker",
            uuid="test-uuid",
            backend_type="dlna",
            dlna_ip="192.168.1.99",
            http_port=8689,
            proxy_port=7120,
        )
        api_client = MagicMock()

        speaker = Speaker(config=speaker_config, api_client=api_client, app_id="app-id")

        with patch.object(
            BackendFactory, "create_from_config", side_effect=Exception("Connection refused")
        ):
            # Should not raise
            result = await speaker.start()

        assert result is False
        assert not speaker._is_running
```

- [ ] Run test to verify it fails:

Run: `pytest tests/test_speaker.py::TestSpeakerLifecycle -v`
Expected: FAIL with `AttributeError: 'Speaker' object has no attribute 'start'`

### Step 2.4: Implement Speaker.start() and Speaker.stop()

- [ ] Add the `start` and `stop` methods to `Speaker` in `qobuz_proxy/speaker.py`:

```python
    async def start(self) -> bool:
        """
        Start all speaker components.

        Returns True on success, False on failure (logged, not raised).
        """
        log_prefix = f"[{self.name}]"
        try:
            component_config = self._build_component_config()

            # 1. Create audio backend
            logger.debug(f"{log_prefix} Creating audio backend...")
            backend = await BackendFactory.create_from_config(component_config)
            self._backend = backend
            logger.info(f"{log_prefix} Connected to backend: {backend.name}")

            # 2. Resolve effective quality
            self._effective_quality = self.config.max_quality
            if self._effective_quality == AUTO_QUALITY:
                if isinstance(backend, DLNABackend):
                    recommended = backend.get_recommended_quality()
                    if recommended:
                        self._effective_quality = recommended
                    else:
                        self._effective_quality = AUTO_FALLBACK_QUALITY
                else:
                    self._effective_quality = 27

            # 3. Create metadata service (per-speaker, shares API client)
            self._metadata_service = MetadataService(
                api_client=self._api_client,
                max_quality=self._effective_quality,
            )

            # 4. Create and start audio proxy server (DLNA only)
            if isinstance(backend, DLNABackend):
                url_provider = MetadataServiceURLProvider(self._metadata_service)
                self._proxy_server = AudioProxyServer(
                    url_provider=url_provider,
                    host=self.config.bind_address,
                    port=self.config.proxy_port,
                )
                await self._proxy_server.start()
                logger.info(
                    f"{log_prefix} Audio proxy on {self.config.bind_address}:{self.config.proxy_port}"
                )
                backend.set_proxy_server(self._proxy_server)

            # 5. Create queue and player
            self._queue = QobuzQueue()
            self._player = QobuzPlayer(
                queue=self._queue,
                metadata_service=self._metadata_service,
                backend=backend,
            )
            if isinstance(backend, DLNABackend):
                self._player.set_fixed_volume_mode(self.config.dlna_fixed_volume)

            # 6. Start discovery service
            self._discovery = DiscoveryService(
                config=component_config,
                app_id=self._app_id,
                on_connect=self._on_app_connected,
                quality_getter=self._get_effective_quality,
            )
            await self._discovery.start()
            logger.info(
                f"{log_prefix} Discovery on port {self.config.http_port}, "
                f"visible as '{self.name}'"
            )

            self._is_running = True
            return True

        except Exception as e:
            logger.error(f"{log_prefix} Failed to start: {e}", exc_info=True)
            # Clean up any partially started components
            await self.stop()
            return False

    async def stop(self) -> None:
        """Stop all speaker components."""
        log_prefix = f"[{self.name}]"
        if self._state_reporter:
            try:
                await self._state_reporter.stop()
            except Exception as e:
                logger.warning(f"{log_prefix} Error stopping state reporter: {e}")

        if self._player:
            try:
                await self._player.stop()
            except Exception as e:
                logger.warning(f"{log_prefix} Error stopping player: {e}")

        if self._ws_manager:
            try:
                await self._ws_manager.stop()
            except Exception as e:
                logger.warning(f"{log_prefix} Error stopping WebSocket: {e}")

        if self._discovery:
            try:
                await self._discovery.stop()
            except Exception as e:
                logger.warning(f"{log_prefix} Error stopping discovery: {e}")

        if self._proxy_server:
            try:
                await self._proxy_server.stop()
            except Exception as e:
                logger.warning(f"{log_prefix} Error stopping proxy: {e}")

        if self._backend:
            try:
                await self._backend.disconnect()
            except Exception as e:
                logger.warning(f"{log_prefix} Error disconnecting backend: {e}")

        self._is_running = False

    def _get_effective_quality(self) -> int:
        """Get current effective quality setting."""
        return self._effective_quality

    def _on_app_connected(self, tokens: ConnectTokens) -> None:
        """Callback when Qobuz app connects with tokens."""
        logger.info(f"[{self.name}] Qobuz app connected, setting up WebSocket...")
        asyncio.create_task(self._setup_websocket(tokens))

    async def _setup_websocket(self, tokens: ConnectTokens) -> None:
        """Set up WebSocket connection after receiving tokens."""
        assert self._queue is not None
        assert self._player is not None

        log_prefix = f"[{self.name}]"

        async with self._ws_setup_lock:
            try:
                if self._ws_manager is not None:
                    self._ws_manager.set_tokens(tokens)
                    logger.info(f"{log_prefix} Refreshed WebSocket tokens")
                    self._ws_connected_event.set()
                    return

                component_config = self._build_component_config()
                self._ws_manager = WsManager(config=component_config)
                self._ws_manager.set_tokens(tokens)
                self._ws_manager.set_max_audio_quality(self._effective_quality)

                # Create handlers
                self._queue_handler = QueueHandler(self._queue)
                self._playback_handler = PlaybackCommandHandler(
                    self._player,
                    on_quality_change=self._on_quality_change,
                )
                self._volume_handler = VolumeCommandHandler(self._player)

                # Wire next track callbacks
                self._player.set_next_track_callbacks(
                    get_callback=self._playback_handler.get_next_track_info,
                    clear_callback=self._playback_handler.clear_next_track_info,
                )

                # Register handlers
                for msg_type in self._queue_handler.get_message_types():
                    self._ws_manager.register_handler(
                        msg_type,
                        lambda mt, msg, h=self._queue_handler: asyncio.create_task(
                            h.handle_message(mt, msg)
                        ),
                    )
                for msg_type in self._playback_handler.get_message_types():
                    self._ws_manager.register_handler(
                        msg_type,
                        lambda mt, msg, h=self._playback_handler: asyncio.create_task(
                            h.handle_message(mt, msg)
                        ),
                    )
                for msg_type in self._volume_handler.get_message_types():
                    self._ws_manager.register_handler(
                        msg_type,
                        lambda mt, msg, h=self._volume_handler: asyncio.create_task(
                            h.handle_message(mt, msg)
                        ),
                    )

                self._ws_manager.register_handler(1, self._handle_protocol_error)

                # State reporter
                self._state_reporter = StateReporter(
                    player=self._player,
                    queue=self._queue,
                    send_callback=self._send_state_report,
                )
                self._player.set_state_reporter(self._state_reporter)
                self._player.set_volume_report_callback(self._ws_manager.send_volume_changed)
                self._player.set_file_quality_report_callback(
                    self._ws_manager.send_file_audio_quality_changed
                )

                # Start
                await self._ws_manager.start()
                await self._state_reporter.start()
                await self._player.start()

                # Send initial volume
                try:
                    initial_volume = await self._player.get_volume()
                    await self._ws_manager.send_volume_changed(initial_volume)
                except Exception as e:
                    logger.warning(f"{log_prefix} Failed to send initial volume: {e}")

                self._ws_connected_event.set()
                logger.info(f"{log_prefix} WebSocket connected, player started")

            except Exception as e:
                logger.error(f"{log_prefix} Failed to set up WebSocket: {e}", exc_info=True)

    async def _on_quality_change(self, new_quality: int) -> None:
        """Handle quality change from Qobuz app."""
        if new_quality == self._effective_quality:
            return
        log_prefix = f"[{self.name}]"
        logger.info(f"{log_prefix} Quality changed: {self._effective_quality} -> {new_quality}")
        self._effective_quality = new_quality
        if self._metadata_service:
            self._metadata_service.set_max_quality(new_quality)
        if self._player:
            await self._player.reload_current_track()

    def _handle_protocol_error(self, msg_type: int, msg) -> None:
        """Handle protocol error messages."""
        error = msg.error if msg.HasField("error") else None
        if error:
            logger.error(f"[{self.name}] Protocol error: code={error.code}, message={error.message}")
        else:
            logger.error(f"[{self.name}] Protocol error (type {msg_type})")

    async def _send_state_report(self, report) -> None:
        """Send state report via WebSocket."""
        if not self._ws_manager:
            return
        playing_state = report.playing_state
        if playing_state == PlaybackState.LOADING:
            playing_state = PlaybackState.STOPPED
        elif playing_state == PlaybackState.ERROR:
            playing_state = PlaybackState.STOPPED

        await self._ws_manager.send_state_update(
            playing_state=int(playing_state),
            buffer_state=int(report.buffer_state),
            position_ms=report.position_value_ms,
            duration_ms=report.duration_ms,
            queue_item_id=report.current_queue_item_id,
            queue_version_major=report.queue_version_major,
            queue_version_minor=report.queue_version_minor,
        )
```

- [ ] Run test to verify it passes:

Run: `pytest tests/test_speaker.py -v`
Expected: PASS

### Step 2.5: Commit

- [ ] Commit:

```bash
git add qobuz_proxy/speaker.py tests/test_speaker.py
git commit -m "feat(speaker): add Speaker class for per-speaker lifecycle

- Speaker bundles discovery, WebSocket, backend, player, queue
- Synthesizes per-speaker Config for existing components (zero
  changes to DiscoveryService, WsManager, BackendFactory)
- start() returns bool, logs errors instead of raising
- stop() tears down all components gracefully
- WebSocket setup via callback, same flow as before"
```

---

## Task 3: Refactor QobuzProxy as Orchestrator

**Files:**
- Modify: `qobuz_proxy/app.py`
- Modify: `qobuz_proxy/cli.py`
- Create: `tests/test_multi_speaker.py`

### Step 3.1: Write failing test for multi-speaker QobuzProxy

- [ ] Create `tests/test_multi_speaker.py`:

```python
"""Tests for multi-speaker QobuzProxy orchestration."""

from unittest.mock import AsyncMock, MagicMock, patch

from qobuz_proxy.config import Config, QobuzConfig, SpeakerConfig
from qobuz_proxy.app import QobuzProxy


class TestMultiSpeakerOrchestration:
    async def test_starts_multiple_speakers(self):
        config = Config(
            qobuz=QobuzConfig(email="test@example.com", password="secret"),
            speakers=[
                SpeakerConfig(
                    name="Living Room", uuid="uuid-1", backend_type="dlna",
                    dlna_ip="192.168.1.50", http_port=8689, proxy_port=7120,
                ),
                SpeakerConfig(
                    name="Office", uuid="uuid-2", backend_type="dlna",
                    dlna_ip="192.168.1.51", http_port=8690, proxy_port=7121,
                ),
            ],
        )

        app = QobuzProxy(config)

        with (
            patch("qobuz_proxy.app.auto_fetch_credentials", new_callable=AsyncMock) as mock_creds,
            patch("qobuz_proxy.app.QobuzAPIClient") as mock_api_cls,
            patch("qobuz_proxy.app.Speaker") as mock_speaker_cls,
        ):
            mock_creds.return_value = {"app_id": "id", "app_secret": "secret"}
            mock_api = AsyncMock()
            mock_api.login = AsyncMock(return_value=True)
            mock_api_cls.return_value = mock_api

            mock_speaker1 = AsyncMock()
            mock_speaker1.start = AsyncMock(return_value=True)
            mock_speaker1.name = "Living Room"
            mock_speaker2 = AsyncMock()
            mock_speaker2.start = AsyncMock(return_value=True)
            mock_speaker2.name = "Office"
            mock_speaker_cls.side_effect = [mock_speaker1, mock_speaker2]

            await app.start()

        assert mock_speaker_cls.call_count == 2
        mock_speaker1.start.assert_awaited_once()
        mock_speaker2.start.assert_awaited_once()

    async def test_continues_when_one_speaker_fails(self):
        config = Config(
            qobuz=QobuzConfig(email="test@example.com", password="secret"),
            speakers=[
                SpeakerConfig(
                    name="Good", uuid="uuid-1", backend_type="dlna",
                    dlna_ip="192.168.1.50", http_port=8689, proxy_port=7120,
                ),
                SpeakerConfig(
                    name="Bad", uuid="uuid-2", backend_type="dlna",
                    dlna_ip="192.168.1.99", http_port=8690, proxy_port=7121,
                ),
            ],
        )

        app = QobuzProxy(config)

        with (
            patch("qobuz_proxy.app.auto_fetch_credentials", new_callable=AsyncMock) as mock_creds,
            patch("qobuz_proxy.app.QobuzAPIClient") as mock_api_cls,
            patch("qobuz_proxy.app.Speaker") as mock_speaker_cls,
        ):
            mock_creds.return_value = {"app_id": "id", "app_secret": "secret"}
            mock_api = AsyncMock()
            mock_api.login = AsyncMock(return_value=True)
            mock_api_cls.return_value = mock_api

            mock_good = AsyncMock()
            mock_good.start = AsyncMock(return_value=True)
            mock_good.name = "Good"
            mock_bad = AsyncMock()
            mock_bad.start = AsyncMock(return_value=False)  # fails
            mock_bad.name = "Bad"
            mock_speaker_cls.side_effect = [mock_good, mock_bad]

            await app.start()

        # Should still be running with at least one speaker
        assert app.is_running

    async def test_fails_when_all_speakers_fail(self):
        config = Config(
            qobuz=QobuzConfig(email="test@example.com", password="secret"),
            speakers=[
                SpeakerConfig(
                    name="Bad1", uuid="uuid-1", backend_type="dlna",
                    dlna_ip="192.168.1.99", http_port=8689, proxy_port=7120,
                ),
            ],
        )

        app = QobuzProxy(config)

        with (
            patch("qobuz_proxy.app.auto_fetch_credentials", new_callable=AsyncMock) as mock_creds,
            patch("qobuz_proxy.app.QobuzAPIClient") as mock_api_cls,
            patch("qobuz_proxy.app.Speaker") as mock_speaker_cls,
        ):
            mock_creds.return_value = {"app_id": "id", "app_secret": "secret"}
            mock_api = AsyncMock()
            mock_api.login = AsyncMock(return_value=True)
            mock_api_cls.return_value = mock_api

            mock_bad = AsyncMock()
            mock_bad.start = AsyncMock(return_value=False)
            mock_bad.name = "Bad1"
            mock_speaker_cls.return_value = mock_bad

            import pytest
            with pytest.raises(RuntimeError, match="No speakers started"):
                await app.start()
```

- [ ] Run test to verify it fails:

Run: `pytest tests/test_multi_speaker.py -v`
Expected: FAIL (import errors or assertion failures)

### Step 3.2: Refactor QobuzProxy in app.py

- [ ] Replace `qobuz_proxy/app.py` with the orchestrator version:

```python
"""
QobuzProxy Application.

Main orchestrator that manages shared resources and Speaker instances.
"""

import asyncio
import logging
import signal
from typing import Optional

from qobuz_proxy.config import Config
from qobuz_proxy.auth import (
    QobuzAPIClient,
    AuthenticationError,
    auto_fetch_credentials,
)
from qobuz_proxy.speaker import Speaker

logger = logging.getLogger(__name__)


class QobuzProxy:
    """
    Main QobuzProxy application.

    Orchestrates shared resources (auth, API client) and per-speaker
    lifecycle via Speaker instances.

    Usage:
        config = load_config(...)
        app = QobuzProxy(config)
        await app.run()
    """

    def __init__(self, config: Config):
        self._config = config
        self._is_running = False
        self._shutdown_event = asyncio.Event()

        # Shared resources
        self._api_client: Optional[QobuzAPIClient] = None
        self._app_id: str = ""
        self._app_secret: str = ""

        # Per-speaker instances
        self._speakers: list[Speaker] = []

    async def start(self) -> None:
        """
        Start QobuzProxy and all speakers.

        Raises:
            AuthenticationError: If Qobuz login fails
            RuntimeError: If no speakers start successfully
        """
        logger.info("Starting QobuzProxy...")

        # 1. Fetch app credentials (shared)
        logger.info("Fetching Qobuz app credentials...")
        credentials = await auto_fetch_credentials()
        if not credentials:
            raise AuthenticationError("Failed to fetch Qobuz app credentials")

        self._app_id = credentials["app_id"]
        self._app_secret = credentials["app_secret"]

        # 2. Initialize API client and authenticate (shared)
        self._api_client = QobuzAPIClient(self._app_id, self._app_secret)
        logger.info(f"Authenticating as {self._config.qobuz.email}...")
        if not await self._api_client.login(
            email=self._config.qobuz.email,
            password=self._config.qobuz.password,
        ):
            raise AuthenticationError("Qobuz login failed - check credentials")
        logger.info("Authentication successful")

        # 3. Create and start speakers
        speaker_configs = self._config.speakers
        logger.info(f"Starting {len(speaker_configs)} speaker(s)...")

        self._speakers = [
            Speaker(
                config=sc,
                api_client=self._api_client,
                app_id=self._app_id,
            )
            for sc in speaker_configs
        ]

        # Start all speakers concurrently
        results = await asyncio.gather(
            *(s.start() for s in self._speakers),
            return_exceptions=True,
        )

        # Check results
        started = []
        for speaker, result in zip(self._speakers, results):
            if isinstance(result, Exception):
                logger.error(f"Speaker '{speaker.name}' failed: {result}")
            elif result:
                started.append(speaker)
            else:
                logger.warning(f"Speaker '{speaker.name}' failed to start")

        if not started:
            raise RuntimeError(
                "No speakers started successfully. Check logs for details."
            )

        self._is_running = True
        self._speakers = started  # Keep only started speakers

        for s in self._speakers:
            logger.info(f"Speaker '{s.name}' ready - visible in Qobuz app")

        if len(started) < len(speaker_configs):
            logger.warning(
                f"{len(started)}/{len(speaker_configs)} speakers started"
            )

    async def stop(self) -> None:
        """Stop all speakers."""
        if not self._is_running:
            return

        logger.info("Stopping QobuzProxy...")
        self._is_running = False

        # Stop all speakers concurrently
        await asyncio.gather(
            *(s.stop() for s in self._speakers),
            return_exceptions=True,
        )

        logger.info("QobuzProxy stopped")

    async def run(self) -> None:
        """Run QobuzProxy until interrupted."""
        loop = asyncio.get_running_loop()

        def handle_signal() -> None:
            logger.info("Shutdown signal received")
            self._shutdown_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, handle_signal)

        try:
            await self.start()
            await self._shutdown_event.wait()
        finally:
            await self.stop()

    @property
    def is_running(self) -> bool:
        """Check if the application is running."""
        return self._is_running
```

- [ ] Run test to verify it passes:

Run: `pytest tests/test_multi_speaker.py -v`
Expected: PASS

### Step 3.3: Update cli.py log_config for multi-speaker

- [ ] Replace the `log_config` function in `qobuz_proxy/cli.py`:

```python
def log_config(config: Config) -> None:
    """Log configuration summary (without sensitive data)."""
    for i, sc in enumerate(config.speakers):
        prefix = f"Speaker {i + 1}" if len(config.speakers) > 1 else "Device"
        logger.info(f"{prefix}: {sc.name} ({sc.uuid[:8]}...)")
        if sc.backend_type == "dlna":
            logger.info(f"  DLNA target: {sc.dlna_ip}:{sc.dlna_port}")
            if sc.dlna_fixed_volume:
                logger.info("  Volume control: disabled (fixed_volume=true)")
            logger.info(f"  Proxy server: {sc.bind_address}:{sc.proxy_port}")
        elif sc.backend_type == "local":
            logger.info(f"  Audio device: {sc.audio_device}")
            logger.info(f"  Buffer size: {sc.audio_buffer_size} frames")
        logger.info(f"  HTTP server: {sc.bind_address}:{sc.http_port}")
        logger.info(f"  Max quality: {sc.max_quality}")
```

### Step 3.4: Run all existing tests to verify backwards compatibility

- [ ] Run the full test suite:

Run: `pytest --ignore=tests/backends/test_local_backend.py --ignore=tests/backends/test_local_buffer_status.py --ignore=tests/backends/test_local_config.py --ignore=tests/backends/test_local_device.py --ignore=tests/backends/test_local_ring_buffer.py --ignore=tests/backends/test_local_seek.py --ignore=tests/test_app_local_backend.py -v`
Expected: ALL PASS (141 original + new tests)

### Step 3.5: Commit

- [ ] Commit:

```bash
git add qobuz_proxy/app.py qobuz_proxy/cli.py tests/test_multi_speaker.py
git commit -m "refactor(app): make QobuzProxy a multi-speaker orchestrator

- QobuzProxy now creates Speaker instances from config.speakers
- Shared auth/API client, per-speaker everything else
- Concurrent start with error isolation: failed speakers are
  skipped, process exits only if zero speakers succeed
- Concurrent stop for all speakers
- Updated log_config() to show per-speaker details"
```

---

## Task 4: Config Examples and Documentation

**Files:**
- Modify: `.env.example`
- Modify: `config.yaml.example`

### Step 4.1: Update .env.example

- [ ] Add multi-speaker examples to `.env.example` (append after the existing content):

```
# --- Multi-Speaker Configuration (comma-separated) ---
# When QOBUZPROXY_DEVICE_NAME contains commas, multiple speakers are created.
# Each env var is split by comma; single values broadcast to all speakers.
#
# QOBUZPROXY_DEVICE_NAME=Living Room,Office
# QOBUZPROXY_BACKEND=dlna,dlna
# QOBUZPROXY_DLNA_IP=192.168.1.50,192.168.1.51
# QOBUZ_MAX_QUALITY=auto,7
#
# Ports are auto-assigned (8689+, 7120+) unless specified:
# QOBUZPROXY_HTTP_PORT=8689,8690
# QOBUZPROXY_PROXY_PORT=7120,7121
```

### Step 4.2: Update config.yaml.example

- [ ] Add speakers section to `config.yaml.example` (append after the existing content):

```yaml

# --- Multi-Speaker Configuration ---
# Use 'speakers' instead of 'device'/'backend'/'server' for multiple speakers.
# Each speaker appears as a separate device in the Qobuz app.
# Ports are auto-assigned unless specified.
#
# speakers:
#   - name: "Living Room"
#     backend: dlna
#     dlna_ip: "192.168.1.50"
#     max_quality: auto
#     # http_port: 8689          # optional, auto-assigned
#     # proxy_port: 7120         # optional, auto-assigned
#
#   - name: "Office"
#     backend: dlna
#     dlna_ip: "192.168.1.51"
#     max_quality: 7
#
#   - name: "Headphones"
#     backend: local
#     audio_device: "Built-in Output"
```

### Step 4.3: Commit

- [ ] Commit:

```bash
git add .env.example config.yaml.example
git commit -m "docs: add multi-speaker config examples

- .env.example: comma-separated env var format
- config.yaml.example: speakers list format"
```

---

## Summary

| Task | What | New/Modified Files |
|------|------|--------------------|
| 1 | SpeakerConfig + normalization | `config.py`, `tests/test_speaker_config.py` |
| 2 | Speaker class | `speaker.py`, `tests/test_speaker.py` |
| 3 | QobuzProxy orchestrator | `app.py`, `cli.py`, `tests/test_multi_speaker.py` |
| 4 | Config examples | `.env.example`, `config.yaml.example` |

**Design deviation from spec:** MetadataService is per-speaker instead of shared. The spec proposed sharing it with a cache key of `(track_id, quality)`, but the current MetadataService has a mutable `_max_quality` field and `set_max_quality()` that invalidates all cached URLs. Making it per-speaker avoids any changes to MetadataService internals while still sharing the API client (the actual expensive, stateful resource). Two speakers playing the same track at the same quality will each cache the metadata separately — negligible overhead.

**Unchanged existing components:** DiscoveryService, WsManager, AudioProxyServer, BackendFactory, Player, Queue, all handlers, StateReporter, Protocol. The Speaker class synthesizes a per-speaker `Config` object so these components receive the interface they expect with zero code changes.
