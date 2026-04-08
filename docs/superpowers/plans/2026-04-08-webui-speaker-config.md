# Web UI Speaker Configuration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add full speaker lifecycle management (discover, add, edit, remove, live status) to the web UI, with changes persisted to `config.yaml`.

**Architecture:** REST API endpoints for CRUD and discovery, extending the existing aiohttp routes. Frontend is vanilla JS extending the current single-page app. Config persistence via PyYAML atomic writes. Runtime speaker management via new methods on `QobuzProxy` in `app.py`.

**Tech Stack:** Python 3 / aiohttp / PyYAML / vanilla JS+CSS

---

## File Structure

### New Files

| File | Responsibility |
|---|---|
| `qobuz_proxy/webui/speaker_routes.py` | Speaker CRUD + discovery route handlers |
| `qobuz_proxy/webui/config_writer.py` | Serialize Config to YAML, atomic write |
| `tests/webui/test_speaker_routes.py` | Tests for speaker API endpoints |
| `tests/webui/test_config_writer.py` | Tests for config serialization and persistence |

### Modified Files

| File | Changes |
|---|---|
| `qobuz_proxy/config.py` | Add `slugify_name()`, `speaker_config_to_dict()`, `config_to_dict()` |
| `qobuz_proxy/speaker.py` | Add `get_status()` returning rich playback info |
| `qobuz_proxy/app.py` | Add `_add_speaker()`, `_edit_speaker()`, `_remove_speaker()` methods; enrich `get_speakers` lambda; expose callbacks to web app |
| `qobuz_proxy/webui/routes.py` | Import and call `register_speaker_routes()` |
| `qobuz_proxy/webui/static/index.html` | Speaker management section with add/edit UI |
| `qobuz_proxy/webui/static/style.css` | Speaker card styles, form styles, badge styles |
| `qobuz_proxy/webui/static/app.js` | Speaker rendering, discovery flow, add/edit/remove interactions |

---

## Task 1: Speaker ID Utility and Config Serialization

**Files:**
- Modify: `qobuz_proxy/config.py`
- Create: `qobuz_proxy/webui/config_writer.py`
- Create: `tests/webui/test_config_writer.py`
- Modify: `tests/test_config.py`

### Step 1.1: Write test for `slugify_name()`

- [ ] Add to `tests/test_config.py`:

```python
from qobuz_proxy.config import slugify_name


class TestSlugifyName:
    def test_simple_name(self) -> None:
        assert slugify_name("Living Room") == "living-room"

    def test_special_characters(self) -> None:
        assert slugify_name("Sonos One (Kitchen)") == "sonos-one-kitchen"

    def test_multiple_spaces(self) -> None:
        assert slugify_name("My  Big  Speaker") == "my-big-speaker"

    def test_already_slug(self) -> None:
        assert slugify_name("office") == "office"

    def test_trailing_hyphens(self) -> None:
        assert slugify_name("--test--") == "test"
```

- [ ] Run: `pytest tests/test_config.py::TestSlugifyName -v`
- [ ] Expected: FAIL — `slugify_name` not defined

### Step 1.2: Implement `slugify_name()`

- [ ] Add to `qobuz_proxy/config.py` after the existing `generate_speaker_uuid()` function (line ~233):

```python
def slugify_name(name: str) -> str:
    """Convert a speaker name to a URL-safe slug ID."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower())
    return slug.strip("-")
```

- [ ] Run: `pytest tests/test_config.py::TestSlugifyName -v`
- [ ] Expected: PASS

### Step 1.3: Write test for `speaker_config_to_dict()`

- [ ] Add to `tests/test_config.py`:

```python
from qobuz_proxy.config import speaker_config_to_dict, SpeakerConfig


class TestSpeakerConfigToDict:
    def test_dlna_speaker(self) -> None:
        sc = SpeakerConfig(
            name="Living Room",
            backend_type="dlna",
            max_quality=7,
            dlna_ip="192.168.1.50",
            dlna_port=1400,
            dlna_fixed_volume=False,
            dlna_description_url="http://192.168.1.50:1400/xml/desc.xml",
        )
        d = speaker_config_to_dict(sc)
        assert d["name"] == "Living Room"
        assert d["backend"] == "dlna"
        assert d["max_quality"] == 7
        assert d["dlna_ip"] == "192.168.1.50"
        assert d["dlna_port"] == 1400
        assert d["dlna_fixed_volume"] is False
        assert d["dlna_description_url"] == "http://192.168.1.50:1400/xml/desc.xml"
        # Should not include local-only fields
        assert "audio_device" not in d
        assert "audio_buffer_size" not in d

    def test_local_speaker(self) -> None:
        sc = SpeakerConfig(
            name="Bedroom",
            backend_type="local",
            max_quality=27,
            audio_device="Built-in Output",
            audio_buffer_size=4096,
        )
        d = speaker_config_to_dict(sc)
        assert d["name"] == "Bedroom"
        assert d["backend"] == "local"
        assert d["audio_device"] == "Built-in Output"
        assert d["audio_buffer_size"] == 4096
        # Should not include DLNA-only fields
        assert "dlna_ip" not in d
        assert "dlna_port" not in d

    def test_auto_quality(self) -> None:
        sc = SpeakerConfig(name="Test", max_quality=0)
        d = speaker_config_to_dict(sc)
        assert d["max_quality"] == "auto"
```

- [ ] Run: `pytest tests/test_config.py::TestSpeakerConfigToDict -v`
- [ ] Expected: FAIL

### Step 1.4: Implement `speaker_config_to_dict()`

- [ ] Add to `qobuz_proxy/config.py` after `slugify_name()`:

```python
def speaker_config_to_dict(sc: SpeakerConfig) -> dict[str, Any]:
    """Convert a SpeakerConfig to a YAML-serializable dict.

    Omits backend-specific fields that don't apply (e.g., no DLNA fields for local speakers).
    Auto-assigned fields (uuid, http_port, proxy_port) are omitted since they're regenerated.
    """
    d: dict[str, Any] = {
        "name": sc.name,
        "backend": sc.backend_type,
        "max_quality": "auto" if sc.max_quality == AUTO_QUALITY else sc.max_quality,
    }
    if sc.backend_type == "dlna":
        d["dlna_ip"] = sc.dlna_ip
        d["dlna_port"] = sc.dlna_port
        d["dlna_fixed_volume"] = sc.dlna_fixed_volume
        if sc.dlna_description_url:
            d["dlna_description_url"] = sc.dlna_description_url
    elif sc.backend_type == "local":
        d["audio_device"] = sc.audio_device
        d["audio_buffer_size"] = sc.audio_buffer_size
    return d
```

- [ ] Run: `pytest tests/test_config.py::TestSpeakerConfigToDict -v`
- [ ] Expected: PASS

### Step 1.5: Write test for config writer

- [ ] Create `tests/webui/test_config_writer.py`:

```python
"""Tests for config YAML writer."""

import yaml
from pathlib import Path

from qobuz_proxy.config import Config, SpeakerConfig, ServerConfig
from qobuz_proxy.webui.config_writer import save_config


class TestSaveConfig:
    def test_writes_speakers_to_yaml(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        config = Config(server=ServerConfig(http_port=8689))
        config.speakers = [
            SpeakerConfig(
                name="Living Room",
                backend_type="dlna",
                dlna_ip="192.168.1.50",
                dlna_port=1400,
                max_quality=7,
            ),
        ]
        save_config(config, config_path)

        data = yaml.safe_load(config_path.read_text())
        assert len(data["speakers"]) == 1
        assert data["speakers"][0]["name"] == "Living Room"
        assert data["speakers"][0]["dlna_ip"] == "192.168.1.50"

    def test_atomic_write_no_partial_file(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        config_path.write_text("original content")

        config = Config()
        config.speakers = [SpeakerConfig(name="Test", backend_type="dlna", dlna_ip="10.0.0.1")]
        save_config(config, config_path)

        # File should be completely replaced
        data = yaml.safe_load(config_path.read_text())
        assert data["speakers"][0]["name"] == "Test"

    def test_preserves_server_settings(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        config = Config(server=ServerConfig(http_port=9999, bind_address="127.0.0.1"))
        config.speakers = [SpeakerConfig(name="Test", backend_type="dlna", dlna_ip="10.0.0.1")]
        save_config(config, config_path)

        data = yaml.safe_load(config_path.read_text())
        assert data["server"]["http_port"] == 9999
        assert data["server"]["bind_address"] == "127.0.0.1"

    def test_round_trip_auto_quality(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        config = Config()
        config.speakers = [SpeakerConfig(name="Auto", backend_type="dlna", dlna_ip="10.0.0.1", max_quality=0)]
        save_config(config, config_path)

        data = yaml.safe_load(config_path.read_text())
        assert data["speakers"][0]["max_quality"] == "auto"
```

- [ ] Run: `pytest tests/webui/test_config_writer.py -v`
- [ ] Expected: FAIL — module doesn't exist

### Step 1.6: Implement config writer

- [ ] Create `qobuz_proxy/webui/config_writer.py`:

```python
"""Write Config back to config.yaml with atomic file operations."""

import logging
import os
import tempfile
from pathlib import Path

import yaml

from qobuz_proxy.config import Config, speaker_config_to_dict

logger = logging.getLogger(__name__)


def config_to_dict(config: Config) -> dict:
    """Convert Config to a YAML-serializable dict."""
    d: dict = {}

    # Server settings
    d["server"] = {
        "http_port": config.server.http_port,
        "bind_address": config.server.bind_address,
    }

    # Logging
    d["logging"] = {"level": config.logging.level}

    # Speakers
    d["speakers"] = [speaker_config_to_dict(sc) for sc in config.speakers]

    return d


def save_config(config: Config, path: Path) -> None:
    """Atomically write config to YAML file.

    Writes to a temp file first, then renames to the target path.
    """
    data = config_to_dict(config)

    # Write to temp file in the same directory (for atomic os.replace)
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(dir=parent, suffix=".yaml.tmp")
    try:
        with os.fdopen(fd, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)
        os.replace(tmp_path, path)
        logger.info(f"Config saved to {path}")
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
```

- [ ] Run: `pytest tests/webui/test_config_writer.py -v`
- [ ] Expected: PASS

### Step 1.7: Run full test suite and commit

- [ ] Run: `pytest tests/test_config.py tests/webui/test_config_writer.py -v`
- [ ] Expected: All PASS
- [ ] Commit:

```bash
git add qobuz_proxy/config.py qobuz_proxy/webui/config_writer.py tests/webui/test_config_writer.py tests/test_config.py
git commit -m "feat(webui): add speaker ID slugify, config serialization, and YAML writer"
```

---

## Task 2: Rich Speaker Status

**Files:**
- Modify: `qobuz_proxy/speaker.py`
- Modify: `qobuz_proxy/app.py`
- Modify: `tests/test_speaker.py`

### Step 2.1: Write test for `Speaker.get_status()`

- [ ] Add to `tests/test_speaker.py` (adapt to existing test patterns in that file). Create a test that exercises the `get_status()` method. The method should return a dict with speaker config info and playback state:

```python
from qobuz_proxy.config import SpeakerConfig, slugify_name


class TestSpeakerGetStatus:
    def test_idle_status(self) -> None:
        """Speaker with no player returns idle status."""
        config = SpeakerConfig(
            name="Living Room",
            backend_type="dlna",
            dlna_ip="192.168.1.50",
            dlna_port=1400,
            max_quality=7,
        )
        # Cannot instantiate Speaker without api_client, so test the static helper
        status = _build_speaker_status(config, player=None, backend=None, effective_quality=7)
        assert status["id"] == "living-room"
        assert status["name"] == "Living Room"
        assert status["backend"] == "dlna"
        assert status["status"] == "idle"
        assert status["now_playing"] is None
        assert status["config"]["dlna_ip"] == "192.168.1.50"
```

Note: Since Speaker requires an api_client, the test should test a helper function `_build_speaker_status()` that we'll extract. Alternatively, add the test as part of task integration tests. Adjust test approach based on existing test patterns in `tests/test_speaker.py`.

- [ ] Run test — expected FAIL

### Step 2.2: Implement `get_status()` on Speaker

- [ ] Add to `qobuz_proxy/speaker.py` after the `name` property (around line 105):

```python
def get_status(self) -> dict:
    """Return rich status dict for API responses."""
    status = slugify_name(self._config.name)

    # Determine playback status
    if not self._is_running:
        playback_status = "disconnected"
    elif self._player and self._player.state == PlaybackState.PLAYING:
        playback_status = "playing"
    elif self._player and self._player.state == PlaybackState.PAUSED:
        playback_status = "paused"
    else:
        playback_status = "idle"

    # Build now_playing if there's a current track
    now_playing = None
    if self._player and self._player.current_track and playback_status in ("playing", "paused"):
        track = self._player.current_track
        meta = track.metadata
        now_playing = {
            "title": meta.get("title", ""),
            "artist": meta.get("artist", ""),
            "album": meta.get("album", ""),
            "album_art_url": meta.get("artwork_url", ""),
            "quality": meta.get("quality_name", ""),
            "volume": self._player._volume,
        }

    # Build config section
    config_dict: dict = {"max_quality": self._effective_quality}
    if self._config.backend_type == "dlna":
        config_dict["dlna_ip"] = self._config.dlna_ip
        config_dict["dlna_port"] = self._config.dlna_port
        config_dict["description_url"] = self._config.dlna_description_url
        config_dict["fixed_volume"] = self._config.dlna_fixed_volume
    elif self._config.backend_type == "local":
        config_dict["audio_device"] = self._config.audio_device
        config_dict["buffer_size"] = self._config.audio_buffer_size

    return {
        "id": slugify_name(self._config.name),
        "name": self._config.name,
        "backend": self._config.backend_type,
        "status": playback_status,
        "config": config_dict,
        "now_playing": now_playing,
    }
```

- [ ] Add the import at the top of `speaker.py`:

```python
from qobuz_proxy.config import slugify_name
```

(The existing imports from config already import `SpeakerConfig` etc., just add `slugify_name` to that import line.)

- [ ] Run test — expected PASS

### Step 2.3: Update `get_speakers` lambda in app.py

- [ ] In `qobuz_proxy/app.py`, replace the `get_speakers` lambda (line 257-259):

```python
# Old:
self._web_app["get_speakers"] = lambda: [
    {"name": s.name, "status": "running", "connected": True} for s in self._speakers
]

# New:
self._web_app["get_speakers"] = lambda: [s.get_status() for s in self._speakers]
```

### Step 2.4: Update existing test for status endpoint

- [ ] In `tests/webui/test_routes.py`, update the `authed_client` fixture's `get_speakers` lambda to return the new shape:

```python
app["get_speakers"] = lambda: [
    {
        "id": "living-room",
        "name": "Living Room",
        "backend": "dlna",
        "status": "playing",
        "config": {"dlna_ip": "192.168.1.50", "dlna_port": 1400, "max_quality": 7},
        "now_playing": {
            "title": "Test Track",
            "artist": "Test Artist",
            "album": "Test Album",
            "album_art_url": "",
            "quality": "Hi-Res 96kHz",
            "volume": 50,
        },
    }
]
```

- [ ] Update `test_status_authenticated` to check the new shape:

```python
async def test_status_authenticated(authed_client: TestClient) -> None:
    resp = await authed_client.get("/api/status")
    assert resp.status == 200
    data = await resp.json()
    assert data["auth"]["authenticated"] is True
    assert len(data["speakers"]) == 1
    speaker = data["speakers"][0]
    assert speaker["id"] == "living-room"
    assert speaker["name"] == "Living Room"
    assert speaker["status"] == "playing"
    assert speaker["now_playing"]["title"] == "Test Track"
```

- [ ] Run: `pytest tests/webui/test_routes.py -v`
- [ ] Expected: PASS

### Step 2.5: Commit

```bash
git add qobuz_proxy/speaker.py qobuz_proxy/app.py tests/test_speaker.py tests/webui/test_routes.py
git commit -m "feat(webui): add rich speaker status with now-playing metadata"
```

---

## Task 3: DLNA Discovery API Endpoint

**Files:**
- Create: `qobuz_proxy/webui/speaker_routes.py`
- Modify: `qobuz_proxy/webui/routes.py`
- Create: `tests/webui/test_speaker_routes.py`

### Step 3.1: Write test for DLNA discovery endpoint

- [ ] Create `tests/webui/test_speaker_routes.py`:

```python
"""Tests for speaker management API routes."""

import pytest
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from qobuz_proxy.webui.routes import register_routes


def make_app() -> web.Application:
    app = web.Application()
    app["auth_state"] = {"authenticated": True, "user_id": "12345", "email": "user@example.com"}
    app["get_speakers"] = lambda: []
    app["version"] = "1.2.1"
    app["on_auth_token"] = AsyncMock(return_value=True)
    app["on_logout"] = AsyncMock()
    app["on_add_speaker"] = AsyncMock(return_value={"id": "test", "name": "Test"})
    app["on_edit_speaker"] = AsyncMock(return_value={"id": "test", "name": "Test"})
    app["on_remove_speaker"] = AsyncMock(return_value=True)
    app["local_audio_enabled"] = False
    register_routes(app)
    return app


@pytest.fixture
async def client():
    async with TestClient(TestServer(make_app())) as c:
        yield c


class TestDiscoverDLNA:
    async def test_discover_returns_devices(self, client: TestClient) -> None:
        mock_devices = [
            AsyncMock(
                friendly_name="Sonos One",
                ip="192.168.1.50",
                port=1400,
                model_name="Sonos One",
                manufacturer="Sonos, Inc.",
                udn="uuid:123",
                location="http://192.168.1.50:1400/xml/desc.xml",
            )
        ]
        with patch(
            "qobuz_proxy.webui.speaker_routes.discover_dlna_devices",
            new_callable=AsyncMock,
            return_value=mock_devices,
        ):
            resp = await client.post("/api/discover/dlna", json={})
            assert resp.status == 200
            data = await resp.json()
            assert data["count"] == 1
            assert data["devices"][0]["friendly_name"] == "Sonos One"
            assert data["devices"][0]["ip"] == "192.168.1.50"
            assert data["devices"][0]["location"] == "http://192.168.1.50:1400/xml/desc.xml"

    async def test_discover_with_timeout(self, client: TestClient) -> None:
        with patch(
            "qobuz_proxy.webui.speaker_routes.discover_dlna_devices",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_discover:
            resp = await client.post("/api/discover/dlna", json={"timeout": 10})
            assert resp.status == 200
            mock_discover.assert_awaited_once_with(timeout=10.0)

    async def test_discover_empty(self, client: TestClient) -> None:
        with patch(
            "qobuz_proxy.webui.speaker_routes.discover_dlna_devices",
            new_callable=AsyncMock,
            return_value=[],
        ):
            resp = await client.post("/api/discover/dlna", json={})
            assert resp.status == 200
            data = await resp.json()
            assert data["count"] == 0
            assert data["devices"] == []
```

- [ ] Run: `pytest tests/webui/test_speaker_routes.py::TestDiscoverDLNA -v`
- [ ] Expected: FAIL — module doesn't exist

### Step 3.2: Implement discovery endpoint

- [ ] Create `qobuz_proxy/webui/speaker_routes.py`:

```python
"""Speaker management API routes: CRUD and discovery."""

import logging
from typing import Any

from aiohttp import web

from qobuz_proxy.backends.dlna.discovery import discover_dlna_devices

logger = logging.getLogger(__name__)


async def _handle_discover_dlna(request: web.Request) -> web.Response:
    """Trigger SSDP discovery and return found DLNA devices."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    timeout = float(body.get("timeout", 5))
    devices = await discover_dlna_devices(timeout=timeout)

    result = {
        "devices": [
            {
                "friendly_name": d.friendly_name,
                "ip": d.ip,
                "port": d.port,
                "model_name": d.model_name,
                "manufacturer": d.manufacturer,
                "udn": d.udn,
                "location": d.location,
            }
            for d in devices
        ],
        "count": len(devices),
    }
    return web.json_response(result)


def register_speaker_routes(app: web.Application) -> None:
    """Register speaker management routes."""
    app.router.add_post("/api/discover/dlna", _handle_discover_dlna)
```

- [ ] In `qobuz_proxy/webui/routes.py`, import and call at the end of `register_routes()`:

```python
from qobuz_proxy.webui.speaker_routes import register_speaker_routes
```

And add to `register_routes()` body (after the existing routes, before `add_static`):

```python
register_speaker_routes(app)
```

- [ ] Run: `pytest tests/webui/test_speaker_routes.py::TestDiscoverDLNA -v`
- [ ] Expected: PASS

### Step 3.3: Commit

```bash
git add qobuz_proxy/webui/speaker_routes.py qobuz_proxy/webui/routes.py tests/webui/test_speaker_routes.py
git commit -m "feat(webui): add DLNA discovery API endpoint"
```

---

## Task 4: Local Audio Discovery Endpoint

**Files:**
- Modify: `qobuz_proxy/webui/speaker_routes.py`
- Modify: `tests/webui/test_speaker_routes.py`

### Step 4.1: Write test for audio device listing

- [ ] Add to `tests/webui/test_speaker_routes.py`:

```python
class TestDiscoverAudioDevices:
    async def test_returns_404_when_disabled(self, client: TestClient) -> None:
        resp = await client.get("/api/discover/audio-devices")
        assert resp.status == 404

    async def test_returns_devices_when_enabled(self) -> None:
        app = make_app()
        app["local_audio_enabled"] = True
        async with TestClient(TestServer(app)) as c:
            with patch(
                "qobuz_proxy.webui.speaker_routes.list_audio_devices",
                return_value=[
                    AsyncMock(
                        index=0,
                        name="Built-in Output",
                        channels=2,
                        default_samplerate=44100.0,
                        is_default=True,
                    ),
                ],
            ):
                resp = await c.get("/api/discover/audio-devices")
                assert resp.status == 200
                data = await resp.json()
                assert len(data["devices"]) == 1
                assert data["devices"][0]["name"] == "Built-in Output"
                assert data["devices"][0]["is_default"] is True
```

- [ ] Run: `pytest tests/webui/test_speaker_routes.py::TestDiscoverAudioDevices -v`
- [ ] Expected: FAIL

### Step 4.2: Implement audio device listing endpoint

- [ ] Add to `qobuz_proxy/webui/speaker_routes.py`:

```python
async def _handle_discover_audio_devices(request: web.Request) -> web.Response:
    """List local audio output devices. Returns 404 if disabled."""
    if not request.app.get("local_audio_enabled", False):
        return web.json_response({"error": "local_audio_ui_disabled"}, status=404)

    try:
        from qobuz_proxy.backends.local.device import list_audio_devices

        devices = list_audio_devices()
        result = {
            "devices": [
                {
                    "name": d.name,
                    "index": d.index,
                    "channels": d.channels,
                    "sample_rate": int(d.default_samplerate),
                    "is_default": d.is_default,
                }
                for d in devices
            ],
        }
        return web.json_response(result)
    except ImportError:
        return web.json_response(
            {"error": "local_audio_dependencies_missing"}, status=404
        )
```

- [ ] Add to `register_speaker_routes()`:

```python
app.router.add_get("/api/discover/audio-devices", _handle_discover_audio_devices)
```

- [ ] Run: `pytest tests/webui/test_speaker_routes.py::TestDiscoverAudioDevices -v`
- [ ] Expected: PASS

### Step 4.3: Commit

```bash
git add qobuz_proxy/webui/speaker_routes.py tests/webui/test_speaker_routes.py
git commit -m "feat(webui): add local audio device discovery endpoint"
```

---

## Task 5: Speaker CRUD API Endpoints

**Files:**
- Modify: `qobuz_proxy/webui/speaker_routes.py`
- Modify: `tests/webui/test_speaker_routes.py`

### Step 5.1: Write tests for CRUD endpoints

- [ ] Add to `tests/webui/test_speaker_routes.py`:

```python
class TestSpeakerCRUD:
    async def test_add_dlna_speaker(self, client: TestClient) -> None:
        client.app["on_add_speaker"] = AsyncMock(  # type: ignore[union-attr]
            return_value={
                "id": "living-room",
                "name": "Living Room",
                "backend": "dlna",
                "status": "idle",
                "config": {"dlna_ip": "192.168.1.50", "dlna_port": 1400, "max_quality": 7},
                "now_playing": None,
            }
        )
        resp = await client.post(
            "/api/speakers",
            json={
                "name": "Living Room",
                "backend": "dlna",
                "dlna_ip": "192.168.1.50",
                "dlna_port": 1400,
                "max_quality": 7,
            },
        )
        assert resp.status == 201
        data = await resp.json()
        assert data["id"] == "living-room"
        assert data["name"] == "Living Room"

    async def test_add_speaker_missing_name(self, client: TestClient) -> None:
        resp = await client.post(
            "/api/speakers",
            json={"backend": "dlna", "dlna_ip": "192.168.1.50"},
        )
        assert resp.status == 400

    async def test_add_speaker_callback_error(self, client: TestClient) -> None:
        client.app["on_add_speaker"] = AsyncMock(  # type: ignore[union-attr]
            side_effect=ValueError("Duplicate speaker name")
        )
        resp = await client.post(
            "/api/speakers",
            json={"name": "Dup", "backend": "dlna", "dlna_ip": "10.0.0.1"},
        )
        assert resp.status == 400
        data = await resp.json()
        assert "Duplicate" in data["error"]

    async def test_edit_speaker(self, client: TestClient) -> None:
        client.app["on_edit_speaker"] = AsyncMock(  # type: ignore[union-attr]
            return_value={
                "id": "living-room",
                "name": "Living Room",
                "backend": "dlna",
                "status": "idle",
                "config": {"dlna_ip": "192.168.1.51", "dlna_port": 1400, "max_quality": 27},
                "now_playing": None,
            }
        )
        resp = await client.put(
            "/api/speakers/living-room",
            json={"name": "Living Room", "dlna_ip": "192.168.1.51", "max_quality": 27},
        )
        assert resp.status == 200

    async def test_edit_speaker_not_found(self, client: TestClient) -> None:
        client.app["on_edit_speaker"] = AsyncMock(  # type: ignore[union-attr]
            side_effect=KeyError("not-found")
        )
        resp = await client.put(
            "/api/speakers/not-found",
            json={"name": "X"},
        )
        assert resp.status == 404

    async def test_remove_speaker(self, client: TestClient) -> None:
        resp = await client.delete("/api/speakers/living-room")
        assert resp.status == 204

    async def test_remove_speaker_not_found(self, client: TestClient) -> None:
        client.app["on_remove_speaker"] = AsyncMock(  # type: ignore[union-attr]
            side_effect=KeyError("not-found")
        )
        resp = await client.delete("/api/speakers/not-found")
        assert resp.status == 404
```

- [ ] Run: `pytest tests/webui/test_speaker_routes.py::TestSpeakerCRUD -v`
- [ ] Expected: FAIL

### Step 5.2: Implement CRUD endpoint handlers

- [ ] Add to `qobuz_proxy/webui/speaker_routes.py`:

```python
from qobuz_proxy.config import slugify_name


async def _handle_get_speakers(request: web.Request) -> web.Response:
    """Return all speakers with config and live status."""
    speakers = request.app["get_speakers"]()
    return web.json_response(speakers)


async def _handle_add_speaker(request: web.Request) -> web.Response:
    """Add a new speaker."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400)

    name = body.get("name", "").strip()
    backend = body.get("backend", "dlna")

    if not name:
        return web.json_response({"error": "name is required"}, status=400)

    if backend == "dlna" and not body.get("dlna_ip"):
        return web.json_response({"error": "dlna_ip is required for DLNA backend"}, status=400)

    callback = request.app["on_add_speaker"]
    try:
        result = await callback(body)
        return web.json_response(result, status=201)
    except (ValueError, Exception) as e:
        return web.json_response({"error": str(e)}, status=400)


async def _handle_edit_speaker(request: web.Request) -> web.Response:
    """Edit an existing speaker."""
    speaker_id = request.match_info["speaker_id"]

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400)

    callback = request.app["on_edit_speaker"]
    try:
        result = await callback(speaker_id, body)
        return web.json_response(result)
    except KeyError:
        return web.json_response({"error": "speaker not found"}, status=404)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)


async def _handle_remove_speaker(request: web.Request) -> web.Response:
    """Remove a speaker."""
    speaker_id = request.match_info["speaker_id"]

    callback = request.app["on_remove_speaker"]
    try:
        await callback(speaker_id)
        return web.Response(status=204)
    except KeyError:
        return web.json_response({"error": "speaker not found"}, status=404)
```

- [ ] Add routes to `register_speaker_routes()`:

```python
app.router.add_get("/api/speakers", _handle_get_speakers)
app.router.add_post("/api/speakers", _handle_add_speaker)
app.router.add_put("/api/speakers/{speaker_id}", _handle_edit_speaker)
app.router.add_delete("/api/speakers/{speaker_id}", _handle_remove_speaker)
```

- [ ] Run: `pytest tests/webui/test_speaker_routes.py::TestSpeakerCRUD -v`
- [ ] Expected: PASS

### Step 5.3: Commit

```bash
git add qobuz_proxy/webui/speaker_routes.py tests/webui/test_speaker_routes.py
git commit -m "feat(webui): add speaker CRUD API endpoints"
```

---

## Task 6: Runtime Speaker Management in app.py

**Files:**
- Modify: `qobuz_proxy/app.py`
- Modify: `qobuz_proxy/config.py` (add `config_path` to Config)
- Modify: `tests/test_app_auth_flow.py` or create `tests/test_app_speaker_mgmt.py`

### Step 6.1: Store config path and expose it

The app needs to know the config file path to write back to it. The simplest approach: store `config_path` on the `Config` object.

- [ ] Add to `Config` dataclass in `qobuz_proxy/config.py` (around line 166):

```python
config_path: Optional[Path] = None  # Set by load_config() when loading from file
```

Add the `Path` import if not already present (it is: `from pathlib import Path`).

- [ ] In `load_config()` (around line 681), after building the config, set the path:

```python
config.config_path = config_path
```

### Step 6.2: Add speaker management methods to app.py

- [ ] Add these imports to `app.py`:

```python
import os

from qobuz_proxy.config import (
    Config,
    SpeakerConfig,
    slugify_name,
    _assign_ports,
    _generate_uuids,
    _validate_speakers,
    AUTO_QUALITY,
)
from qobuz_proxy.webui.config_writer import save_config
```

(Note: `os` may already be imported — add only if missing. `Config` and `SpeakerConfig` can be merged with existing config imports.)

- [ ] Add these methods to the `QobuzProxy` class (after `_on_logout`, before `_start_web_server`):

```python
async def _on_add_speaker(self, body: dict) -> dict:
    """Add a new speaker at runtime."""
    name = body["name"].strip()
    backend_type = body.get("backend", "dlna")

    # Check for duplicate names
    for s in self._speakers:
        if slugify_name(s.name) == slugify_name(name):
            raise ValueError(f"Speaker '{name}' already exists")

    # Build SpeakerConfig
    quality_raw = body.get("max_quality", "auto")
    if isinstance(quality_raw, str) and quality_raw.lower() == "auto":
        max_quality = AUTO_QUALITY
    else:
        max_quality = int(quality_raw)

    sc = SpeakerConfig(
        name=name,
        backend_type=backend_type,
        max_quality=max_quality,
        dlna_ip=body.get("dlna_ip", ""),
        dlna_port=int(body.get("dlna_port", 1400)),
        dlna_fixed_volume=bool(body.get("fixed_volume", False)),
        dlna_description_url=body.get("description_url", ""),
        audio_device=body.get("audio_device", "default"),
        audio_buffer_size=int(body.get("buffer_size", 2048)),
    )

    # Assign ports and UUID
    all_configs = [s._config for s in self._speakers] + [sc]
    _assign_ports(all_configs, webui_port=self._config.server.http_port)
    _generate_uuids([sc])

    # Create and start speaker
    assert self._api_client is not None
    speaker = Speaker(config=sc, api_client=self._api_client, app_id=self._app_id)
    started = await speaker.start()
    if not started:
        raise ValueError(f"Speaker '{name}' failed to start")

    self._speakers.append(speaker)

    # Update config and persist
    self._config.speakers.append(sc)
    self._save_config()

    return speaker.get_status()

async def _on_edit_speaker(self, speaker_id: str, body: dict) -> dict:
    """Edit a speaker at runtime (stop, reconfigure, restart)."""
    # Find speaker by ID
    idx = None
    for i, s in enumerate(self._speakers):
        if slugify_name(s.name) == speaker_id:
            idx = i
            break
    if idx is None:
        raise KeyError(speaker_id)

    old_speaker = self._speakers[idx]
    old_config = self._config.speakers[idx]

    # Build updated SpeakerConfig
    quality_raw = body.get("max_quality", old_config.max_quality)
    if isinstance(quality_raw, str) and quality_raw.lower() == "auto":
        max_quality = AUTO_QUALITY
    else:
        max_quality = int(quality_raw)

    new_config = SpeakerConfig(
        name=body.get("name", old_config.name).strip(),
        uuid=old_config.uuid,
        backend_type=old_config.backend_type,  # Immutable
        max_quality=max_quality,
        http_port=old_config.http_port,
        bind_address=old_config.bind_address,
        dlna_ip=body.get("dlna_ip", old_config.dlna_ip),
        dlna_port=int(body.get("dlna_port", old_config.dlna_port)),
        dlna_fixed_volume=bool(body.get("fixed_volume", old_config.dlna_fixed_volume)),
        dlna_description_url=body.get("description_url", old_config.dlna_description_url),
        proxy_port=old_config.proxy_port,
        audio_device=body.get("audio_device", old_config.audio_device),
        audio_buffer_size=int(body.get("buffer_size", old_config.audio_buffer_size)),
    )

    # Stop old speaker
    await old_speaker.stop()

    # Start new speaker
    assert self._api_client is not None
    new_speaker = Speaker(config=new_config, api_client=self._api_client, app_id=self._app_id)
    started = await new_speaker.start()
    if not started:
        # Try to restart old speaker
        await old_speaker.start()
        raise ValueError(f"Speaker '{new_config.name}' failed to start with new config")

    self._speakers[idx] = new_speaker
    self._config.speakers[idx] = new_config
    self._save_config()

    return new_speaker.get_status()

async def _on_remove_speaker(self, speaker_id: str) -> None:
    """Remove a speaker at runtime."""
    idx = None
    for i, s in enumerate(self._speakers):
        if slugify_name(s.name) == speaker_id:
            idx = i
            break
    if idx is None:
        raise KeyError(speaker_id)

    speaker = self._speakers.pop(idx)
    self._config.speakers.pop(idx)

    await speaker.stop()
    self._save_config()

def _save_config(self) -> None:
    """Persist current config to YAML file."""
    if self._config.config_path:
        try:
            save_config(self._config, self._config.config_path)
        except Exception as e:
            logger.error(f"Failed to save config: {e}")
```

### Step 6.3: Wire callbacks into the web app

- [ ] In `_start_web_server()` (around line 262, after existing callback assignments):

```python
self._web_app["on_add_speaker"] = self._on_add_speaker
self._web_app["on_edit_speaker"] = self._on_edit_speaker
self._web_app["on_remove_speaker"] = self._on_remove_speaker
self._web_app["local_audio_enabled"] = os.environ.get("QOBUZPROXY_LOCAL_AUDIO_UI", "").lower() in ("true", "1", "yes")
```

### Step 6.4: Run existing tests to verify no regressions

- [ ] Run: `pytest tests/ -v --ignore=tests/backends --ignore=tests/connect --ignore=tests/playback`
- [ ] Expected: PASS (some tests may need `on_add_speaker` etc. added to their `make_app()`)

### Step 6.5: Commit

```bash
git add qobuz_proxy/app.py qobuz_proxy/config.py
git commit -m "feat(webui): add runtime speaker add/edit/remove with config persistence"
```

---

## Task 7: Frontend — Rich Speaker Status Display

**Files:**
- Modify: `qobuz_proxy/webui/static/index.html`
- Modify: `qobuz_proxy/webui/static/style.css`
- Modify: `qobuz_proxy/webui/static/app.js`

### Step 7.1: Update HTML structure

- [ ] In `index.html`, replace the speakers section (lines 66-71):

```html
<!-- Speakers Section -->
<div class="card" id="speakers-section">
    <div class="speakers-header">
        <h2>Speakers</h2>
        <button id="add-speaker-btn" onclick="showAddSpeaker()" style="display: none;">+ Add Speaker</button>
    </div>
    <div id="speakers-list">
        <p class="muted">Waiting for authentication...</p>
    </div>
    <div id="add-speaker-panel" style="display: none;"></div>
    <div id="speaker-error" class="error-message" style="display: none;"></div>
</div>
```

### Step 7.2: Add speaker card CSS

- [ ] Append to `style.css`:

```css
/* Speakers header with add button */

.speakers-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 12px;
}

.speakers-header h2 {
    margin-bottom: 0;
}

.speakers-header button {
    font-size: 13px;
    padding: 6px 12px;
}

/* Speaker card */

.speaker-card {
    background: #111111;
    border: 1px solid #2a2a2a;
    border-radius: 8px;
    padding: 12px;
    margin-bottom: 8px;
}

.speaker-card-playing {
    display: flex;
    gap: 12px;
}

.speaker-album-art {
    width: 72px;
    height: 72px;
    border-radius: 6px;
    flex-shrink: 0;
    background: #252525;
    object-fit: cover;
}

.speaker-album-art-placeholder {
    width: 72px;
    height: 72px;
    border-radius: 6px;
    flex-shrink: 0;
    background: #252525;
    display: flex;
    align-items: center;
    justify-content: center;
    color: #555;
    font-size: 24px;
}

.speaker-info {
    flex: 1;
    min-width: 0;
}

.speaker-header {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-bottom: 4px;
    flex-wrap: wrap;
}

.speaker-name {
    font-weight: 600;
    color: #ffffff;
    font-size: 15px;
}

.speaker-badge {
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 10px;
    white-space: nowrap;
}

.badge-playing {
    background: #1b3a1b;
    color: #6fbf6f;
}

.badge-paused {
    background: #3a3a1b;
    color: #bfbf6f;
}

.badge-idle {
    background: #2a2a2a;
    color: #888888;
}

.badge-disconnected {
    background: #3a1b1b;
    color: #bf6f6f;
}

.badge-dlna {
    background: #1a2a3a;
    color: #6fa8dc;
}

.badge-local {
    background: #2a1a3a;
    color: #b07ab0;
}

.speaker-track {
    color: #e0e0e0;
    font-size: 14px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

.speaker-artist-album {
    color: #888888;
    font-size: 13px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

.speaker-meta {
    display: flex;
    gap: 12px;
    color: #666666;
    font-size: 12px;
    margin-top: 4px;
}

.speaker-actions {
    display: flex;
    gap: 4px;
    flex-shrink: 0;
    align-self: flex-start;
}

.speaker-actions button {
    background: #1a1a1a;
    border: 1px solid #333;
    color: #aaa;
    padding: 4px 10px;
    border-radius: 4px;
    font-size: 12px;
}

.speaker-actions button:hover {
    background: #252525;
    color: #fff;
}

.speaker-idle-info {
    color: #666666;
    font-size: 13px;
}

/* Edit form (inline) */

.speaker-edit-card {
    background: #111111;
    border: 2px solid #1976d2;
    border-radius: 8px;
    padding: 16px;
    margin-bottom: 8px;
}

.speaker-edit-card .form-group {
    margin-bottom: 10px;
}

.speaker-edit-card .form-group input,
.speaker-edit-card .form-group select {
    width: 100%;
    background: #0f0f0f;
    border: 1px solid #333;
    border-radius: 6px;
    padding: 8px 10px;
    color: #e0e0e0;
    font-size: 14px;
}

.speaker-edit-card .form-row {
    display: flex;
    gap: 8px;
}

.speaker-edit-card .form-row .form-group {
    flex: 1;
}

.speaker-edit-card .form-group input[type="checkbox"] {
    width: auto;
}

/* Add speaker panel */

#add-speaker-panel {
    margin-top: 12px;
    border-top: 1px solid #2a2a2a;
    padding-top: 12px;
}

.add-step-header {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 10px;
}

.step-number {
    background: #1976d2;
    color: #fff;
    width: 22px;
    height: 22px;
    border-radius: 11px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 12px;
    font-weight: 600;
    flex-shrink: 0;
}

.backend-cards {
    display: flex;
    gap: 8px;
    margin-bottom: 12px;
}

.backend-card {
    flex: 1;
    background: #1a1a1a;
    border: 2px solid #2a2a2a;
    border-radius: 8px;
    padding: 12px;
    cursor: pointer;
    transition: border-color 0.15s;
}

.backend-card:hover {
    border-color: #555;
}

.backend-card.selected {
    border-color: #1976d2;
}

.backend-card h3 {
    font-size: 14px;
    margin-bottom: 4px;
}

.backend-card p {
    font-size: 12px;
    color: #888;
    margin: 0;
}

.device-list {
    display: flex;
    flex-direction: column;
    gap: 6px;
    margin-bottom: 12px;
}

.device-item {
    background: #1a1a1a;
    border: 2px solid #2a2a2a;
    border-radius: 8px;
    padding: 10px 14px;
    cursor: pointer;
    transition: border-color 0.15s;
}

.device-item:hover {
    border-color: #555;
}

.device-item.selected {
    border-color: #1976d2;
}

.device-item-name {
    font-weight: 500;
    color: #fff;
}

.device-item-detail {
    font-size: 12px;
    color: #666;
}

.scan-status {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 8px;
    font-size: 13px;
    color: #888;
}

.manual-entry-link {
    color: #42a5f5;
    font-size: 13px;
    cursor: pointer;
    border: none;
    background: none;
    padding: 0;
}

.manual-entry-link:hover {
    text-decoration: underline;
    background: none;
}
```

### Step 7.3: Update app.js with new speaker rendering

- [ ] Replace the entire contents of `app.js` with the following. This is a full rewrite that preserves all existing auth functionality and adds speaker management:

```javascript
(function () {
    "use strict";

    var pollTimer = null;
    var editingSpeakerId = null; // Track which speaker is being edited
    var addPanelVisible = false;
    var lastSpeakersJson = ""; // Avoid re-rendering when nothing changed

    // ---- Auth state (unchanged from original) ----

    function showAuthState(state) {
        document.getElementById("auth-disconnected").style.display = "none";
        document.getElementById("auth-login").style.display = "none";
        document.getElementById("auth-connected").style.display = "none";

        if (state === "disconnected") {
            document.getElementById("auth-disconnected").style.display = "";
        } else if (state === "login") {
            document.getElementById("auth-login").style.display = "";
        } else if (state === "connected") {
            document.getElementById("auth-connected").style.display = "";
        }
    }

    // ---- Helpers ----

    function escapeHtml(text) {
        var div = document.createElement("div");
        div.appendChild(document.createTextNode(text || ""));
        return div.innerHTML;
    }

    function showError(msg) {
        var el = document.getElementById("speaker-error");
        el.textContent = msg;
        el.style.display = "";
        setTimeout(function () { el.style.display = "none"; }, 5000);
    }

    // ---- Speaker rendering ----

    function renderSpeakerCard(s) {
        // If this speaker is being edited, render the edit form instead
        if (editingSpeakerId === s.id) {
            return renderEditForm(s);
        }

        var isPlaying = s.status === "playing" || s.status === "paused";
        var html = '<div class="speaker-card">';

        if (isPlaying && s.now_playing) {
            html += '<div class="speaker-card-playing">';
            // Album art
            if (s.now_playing.album_art_url) {
                html += '<img class="speaker-album-art" src="' + escapeHtml(s.now_playing.album_art_url) + '" alt="">';
            } else {
                html += '<div class="speaker-album-art-placeholder">&#9835;</div>';
            }
            html += '<div class="speaker-info">';
            html += renderSpeakerHeader(s);
            html += '<div class="speaker-track">' + escapeHtml(s.now_playing.title) + '</div>';
            html += '<div class="speaker-artist-album">' + escapeHtml(s.now_playing.artist);
            if (s.now_playing.album) {
                html += ' &mdash; ' + escapeHtml(s.now_playing.album);
            }
            html += '</div>';
            html += '<div class="speaker-meta">';
            if (s.now_playing.quality) html += '<span>' + escapeHtml(s.now_playing.quality) + '</span>';
            if (s.now_playing.volume !== undefined) html += '<span>Vol ' + s.now_playing.volume + '%</span>';
            html += '</div>';
            html += '</div>'; // speaker-info
            html += renderActions(s);
            html += '</div>'; // speaker-card-playing
        } else {
            // Idle or disconnected
            html += '<div style="display:flex;justify-content:space-between;align-items:center;">';
            html += '<div>';
            html += renderSpeakerHeader(s);
            var detail = "";
            if (s.backend === "dlna" && s.config) {
                detail = escapeHtml(s.config.dlna_ip + ":" + s.config.dlna_port);
            } else if (s.backend === "local" && s.config) {
                detail = escapeHtml(s.config.audio_device || "default");
            }
            if (s.config && s.config.max_quality) {
                var q = s.config.max_quality;
                var qName = q === 0 || q === "auto" ? "Auto" : q === 27 ? "Hi-Res 192k" : q === 7 ? "Hi-Res 96k" : q === 6 ? "CD" : q === 5 ? "MP3" : q;
                detail += " · " + qName + " quality";
            }
            if (detail) html += '<div class="speaker-idle-info">' + detail + '</div>';
            html += '</div>';
            html += renderActions(s);
            html += '</div>';
        }

        html += '</div>'; // speaker-card
        return html;
    }

    function renderSpeakerHeader(s) {
        var badgeClass = "badge-" + s.status;
        var statusLabel = s.status === "playing" ? "▶ Playing" : s.status === "paused" ? "⏸ Paused" : s.status === "idle" ? "Idle" : "Disconnected";
        var backendClass = s.backend === "dlna" ? "badge-dlna" : "badge-local";
        var html = '<div class="speaker-header">';
        html += '<span class="speaker-name">' + escapeHtml(s.name) + '</span>';
        html += '<span class="speaker-badge ' + badgeClass + '">' + statusLabel + '</span>';
        html += '<span class="speaker-badge ' + backendClass + '">' + escapeHtml(s.backend.toUpperCase()) + '</span>';
        html += '</div>';
        return html;
    }

    function renderActions(s) {
        return '<div class="speaker-actions">' +
            '<button onclick="editSpeaker(\'' + escapeHtml(s.id) + '\')">Edit</button>' +
            '<button onclick="removeSpeaker(\'' + escapeHtml(s.id) + '\')">Remove</button>' +
            '</div>';
    }

    function renderEditForm(s) {
        var c = s.config || {};
        var html = '<div class="speaker-edit-card">';
        html += '<div class="speaker-header" style="margin-bottom:12px;">';
        html += '<span class="speaker-name">Editing: ' + escapeHtml(s.name) + '</span>';
        var backendClass = s.backend === "dlna" ? "badge-dlna" : "badge-local";
        html += '<span class="speaker-badge ' + backendClass + '">' + escapeHtml(s.backend.toUpperCase()) + '</span>';
        html += '</div>';

        html += '<div class="form-group"><label>Speaker Name</label>';
        html += '<input type="text" id="edit-name" value="' + escapeHtml(s.name) + '"></div>';

        if (s.backend === "dlna") {
            html += '<div class="form-row">';
            html += '<div class="form-group"><label>IP Address</label><input type="text" id="edit-dlna-ip" value="' + escapeHtml(c.dlna_ip || "") + '"></div>';
            html += '<div class="form-group"><label>Port</label><input type="text" id="edit-dlna-port" value="' + (c.dlna_port || 1400) + '"></div>';
            html += '</div>';
            html += '<div class="form-group"><label>Description URL <span style="color:#666">(optional)</span></label>';
            html += '<input type="text" id="edit-desc-url" value="' + escapeHtml(c.description_url || "") + '"></div>';
            html += '<div class="form-group"><label style="display:inline;"><input type="checkbox" id="edit-fixed-vol"' + (c.fixed_volume ? " checked" : "") + '> Fixed volume</label></div>';
        } else {
            html += '<div class="form-group"><label>Audio Device</label><input type="text" id="edit-audio-device" value="' + escapeHtml(c.audio_device || "default") + '"></div>';
            html += '<div class="form-group"><label>Buffer Size</label><input type="text" id="edit-buffer-size" value="' + (c.buffer_size || 2048) + '"></div>';
        }

        html += '<div class="form-group"><label>Max Quality</label><select id="edit-quality">';
        var qualities = [["auto", "Auto (detect from device)"], ["27", "Hi-Res 192kHz"], ["7", "Hi-Res 96kHz"], ["6", "CD 44.1kHz"], ["5", "MP3 320kbps"]];
        var curQ = String(c.max_quality === 0 ? "auto" : c.max_quality || "auto");
        for (var i = 0; i < qualities.length; i++) {
            var sel = qualities[i][0] === curQ ? " selected" : "";
            html += '<option value="' + qualities[i][0] + '"' + sel + '>' + qualities[i][1] + '</option>';
        }
        html += '</select></div>';

        html += '<div class="button-group" style="justify-content:flex-end;">';
        html += '<button class="button-secondary" onclick="cancelEdit()">Cancel</button>';
        html += '<button onclick="submitEditSpeaker(\'' + escapeHtml(s.id) + '\', \'' + escapeHtml(s.backend) + '\')">Save Changes</button>';
        html += '</div>';
        html += '</div>';
        return html;
    }

    function updateSpeakers(speakers) {
        var container = document.getElementById("speakers-list");

        // Show add button when authenticated
        document.getElementById("add-speaker-btn").style.display = "";

        if (!speakers || speakers.length === 0) {
            if (!addPanelVisible) {
                container.innerHTML = '<p class="muted">No speakers configured. Click "+ Add Speaker" to get started.</p>';
            }
            return;
        }

        // Skip re-render if nothing changed and not editing
        var json = JSON.stringify(speakers);
        if (json === lastSpeakersJson && !editingSpeakerId) return;
        lastSpeakersJson = json;

        var html = "";
        for (var i = 0; i < speakers.length; i++) {
            html += renderSpeakerCard(speakers[i]);
        }
        container.innerHTML = html;
    }

    // ---- Add Speaker Flow ----

    function showAddSpeaker() {
        addPanelVisible = true;
        var panel = document.getElementById("add-speaker-panel");
        panel.style.display = "";
        panel.innerHTML = renderStep1();
    }

    function hideAddSpeaker() {
        addPanelVisible = false;
        var panel = document.getElementById("add-speaker-panel");
        panel.style.display = "none";
        panel.innerHTML = "";
    }

    function renderStep1() {
        var html = '<div class="add-step-header"><span class="step-number">1</span><h3 style="margin:0">Choose Backend Type</h3></div>';
        html += '<div class="backend-cards">';
        html += '<div class="backend-card" onclick="selectBackend(\'dlna\')"><h3>DLNA / UPnP</h3><p>Sonos, Denon HEOS, and other network speakers</p></div>';
        html += '<div class="backend-card" onclick="selectBackend(\'local\')"><h3>Local Audio</h3><p>Play through this computer\'s audio output</p></div>';
        html += '</div>';
        html += '<div class="button-group"><button class="button-secondary" onclick="hideAddSpeaker()">Cancel</button></div>';
        return html;
    }

    function selectBackend(type) {
        var panel = document.getElementById("add-speaker-panel");
        if (type === "dlna") {
            panel.innerHTML = renderStep2DLNA();
            startDLNADiscovery();
        } else {
            panel.innerHTML = renderStep2Local();
            startAudioDeviceDiscovery();
        }
    }

    function renderStep2DLNA() {
        var html = '<div class="add-step-header"><span class="step-number">2</span><h3 style="margin:0">Select DLNA Device</h3></div>';
        html += '<div class="scan-status"><span id="scan-msg">Scanning...</span><button class="button-secondary" onclick="startDLNADiscovery()" style="font-size:12px;padding:4px 10px;">↻ Rescan</button></div>';
        html += '<div id="device-list" class="device-list"></div>';
        html += '<button class="manual-entry-link" onclick="showManualEntry()">Or enter IP address manually</button>';
        html += '<div id="manual-entry" style="display:none;margin-top:8px;">';
        html += '<div class="form-row"><div class="form-group"><label>IP Address</label><input type="text" id="manual-ip" placeholder="192.168.1.50"></div>';
        html += '<div class="form-group"><label>Port</label><input type="text" id="manual-port" value="1400"></div></div>';
        html += '<button onclick="selectManualDevice()" style="font-size:13px;padding:6px 12px;">Use this device</button>';
        html += '</div>';
        html += '<div class="button-group" style="margin-top:12px;"><button class="button-secondary" onclick="showAddSpeaker()">← Back</button></div>';
        return html;
    }

    function renderStep2Local() {
        var html = '<div class="add-step-header"><span class="step-number">2</span><h3 style="margin:0">Select Audio Device</h3></div>';
        html += '<div id="device-list" class="device-list"><p class="muted">Loading devices...</p></div>';
        html += '<div class="button-group" style="margin-top:12px;"><button class="button-secondary" onclick="showAddSpeaker()">← Back</button></div>';
        return html;
    }

    function startDLNADiscovery() {
        var msgEl = document.getElementById("scan-msg");
        var listEl = document.getElementById("device-list");
        if (msgEl) msgEl.textContent = "Scanning...";
        if (listEl) listEl.innerHTML = "";

        fetch("/api/discover/dlna", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ timeout: 5 }),
        })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (msgEl) msgEl.textContent = "Found " + data.count + " device(s)";
                if (!listEl) return;
                if (data.count === 0) {
                    listEl.innerHTML = '<p class="muted">No devices found. Try rescanning or enter IP manually.</p>';
                    return;
                }
                var html = "";
                for (var i = 0; i < data.devices.length; i++) {
                    var d = data.devices[i];
                    html += '<div class="device-item" onclick=\'selectDLNADevice(' + JSON.stringify(d) + ')\'>';
                    html += '<div class="device-item-name">' + escapeHtml(d.friendly_name) + '</div>';
                    html += '<div class="device-item-detail">' + escapeHtml(d.ip + ":" + d.port) + ' · ' + escapeHtml(d.manufacturer) + '</div>';
                    html += '</div>';
                }
                listEl.innerHTML = html;
            })
            .catch(function () {
                if (msgEl) msgEl.textContent = "Scan failed";
            });
    }

    function startAudioDeviceDiscovery() {
        var listEl = document.getElementById("device-list");
        fetch("/api/discover/audio-devices")
            .then(function (r) {
                if (r.status === 404) throw new Error("disabled");
                return r.json();
            })
            .then(function (data) {
                if (!listEl) return;
                if (!data.devices || data.devices.length === 0) {
                    listEl.innerHTML = '<p class="muted">No audio devices found.</p>';
                    return;
                }
                var html = "";
                for (var i = 0; i < data.devices.length; i++) {
                    var d = data.devices[i];
                    html += '<div class="device-item" onclick=\'selectLocalDevice(' + JSON.stringify(d) + ')\'>';
                    html += '<div class="device-item-name">' + escapeHtml(d.name) + (d.is_default ? " (default)" : "") + '</div>';
                    html += '<div class="device-item-detail">' + d.channels + 'ch, ' + d.sample_rate + 'Hz</div>';
                    html += '</div>';
                }
                listEl.innerHTML = html;
            })
            .catch(function () {
                if (listEl) listEl.innerHTML = '<p class="muted">Local audio not available. Install dependencies or enable via QOBUZPROXY_LOCAL_AUDIO_UI.</p>';
            });
    }

    function showManualEntry() {
        document.getElementById("manual-entry").style.display = "";
    }

    function selectManualDevice() {
        var ip = document.getElementById("manual-ip").value.trim();
        var port = document.getElementById("manual-port").value.trim() || "1400";
        if (!ip) return;
        showConfigForm("dlna", { friendly_name: "", ip: ip, port: parseInt(port), location: "" });
    }

    function selectDLNADevice(device) {
        showConfigForm("dlna", device);
    }

    function selectLocalDevice(device) {
        showConfigForm("local", device);
    }

    function showConfigForm(backend, device) {
        var panel = document.getElementById("add-speaker-panel");
        var html = '<div class="add-step-header"><span class="step-number">3</span><h3 style="margin:0">Configure</h3></div>';

        html += '<div class="form-group"><label>Speaker Name</label>';
        html += '<input type="text" id="add-name" value="' + escapeHtml(device.friendly_name || device.name || "") + '"></div>';

        if (backend === "dlna") {
            html += '<div class="form-row">';
            html += '<div class="form-group"><label>IP Address</label><input type="text" id="add-dlna-ip" value="' + escapeHtml(device.ip) + '"></div>';
            html += '<div class="form-group"><label>Port</label><input type="text" id="add-dlna-port" value="' + (device.port || 1400) + '"></div>';
            html += '</div>';
            html += '<div class="form-group"><label>Description URL <span style="color:#666">(optional)</span></label>';
            html += '<input type="text" id="add-desc-url" value="' + escapeHtml(device.location || "") + '" placeholder="Leave empty for auto-discovery"></div>';
            html += '<div class="form-group"><label style="display:inline;"><input type="checkbox" id="add-fixed-vol"> Fixed volume</label></div>';
        } else {
            html += '<input type="hidden" id="add-audio-device" value="' + escapeHtml(device.name || "default") + '">';
            html += '<div class="form-group"><label>Buffer Size</label><input type="text" id="add-buffer-size" value="2048"></div>';
        }

        html += '<div class="form-group"><label>Max Quality</label><select id="add-quality">';
        var qualities = [["auto", "Auto (detect from device)"], ["27", "Hi-Res 192kHz"], ["7", "Hi-Res 96kHz"], ["6", "CD 44.1kHz"], ["5", "MP3 320kbps"]];
        for (var i = 0; i < qualities.length; i++) {
            html += '<option value="' + qualities[i][0] + '">' + qualities[i][1] + '</option>';
        }
        html += '</select></div>';

        html += '<input type="hidden" id="add-backend" value="' + backend + '">';
        html += '<div class="button-group" style="justify-content:flex-end;">';
        html += '<button class="button-secondary" onclick="hideAddSpeaker()">Cancel</button>';
        html += '<button onclick="submitAddSpeaker()">Add Speaker</button>';
        html += '</div>';
        panel.innerHTML = html;
    }

    function submitAddSpeaker() {
        var backend = document.getElementById("add-backend").value;
        var name = document.getElementById("add-name").value.trim();
        if (!name) { showError("Speaker name is required"); return; }

        var body = { name: name, backend: backend, max_quality: document.getElementById("add-quality").value };

        if (backend === "dlna") {
            body.dlna_ip = document.getElementById("add-dlna-ip").value.trim();
            body.dlna_port = parseInt(document.getElementById("add-dlna-port").value) || 1400;
            body.description_url = document.getElementById("add-desc-url").value.trim();
            body.fixed_volume = document.getElementById("add-fixed-vol").checked;
            if (!body.dlna_ip) { showError("IP address is required"); return; }
        } else {
            body.audio_device = document.getElementById("add-audio-device").value;
            body.buffer_size = parseInt(document.getElementById("add-buffer-size").value) || 2048;
        }

        fetch("/api/speakers", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        })
            .then(function (r) {
                if (!r.ok) return r.json().then(function (d) { throw new Error(d.error || "Failed to add speaker"); });
                return r.json();
            })
            .then(function () {
                hideAddSpeaker();
                lastSpeakersJson = ""; // Force re-render
                fetchStatus();
            })
            .catch(function (err) { showError(err.message); });
    }

    // ---- Edit / Remove ----

    function editSpeaker(id) {
        editingSpeakerId = id;
        lastSpeakersJson = ""; // Force re-render
        fetchStatus();
    }

    function cancelEdit() {
        editingSpeakerId = null;
        lastSpeakersJson = ""; // Force re-render
        fetchStatus();
    }

    function submitEditSpeaker(id, backend) {
        var body = {
            name: document.getElementById("edit-name").value.trim(),
            max_quality: document.getElementById("edit-quality").value,
        };
        if (!body.name) { showError("Speaker name is required"); return; }

        if (backend === "dlna") {
            body.dlna_ip = document.getElementById("edit-dlna-ip").value.trim();
            body.dlna_port = parseInt(document.getElementById("edit-dlna-port").value) || 1400;
            body.description_url = document.getElementById("edit-desc-url").value.trim();
            body.fixed_volume = document.getElementById("edit-fixed-vol").checked;
        } else {
            body.audio_device = document.getElementById("edit-audio-device").value;
            body.buffer_size = parseInt(document.getElementById("edit-buffer-size").value) || 2048;
        }

        fetch("/api/speakers/" + encodeURIComponent(id), {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        })
            .then(function (r) {
                if (!r.ok) return r.json().then(function (d) { throw new Error(d.error || "Failed to update speaker"); });
                return r.json();
            })
            .then(function () {
                editingSpeakerId = null;
                lastSpeakersJson = "";
                fetchStatus();
            })
            .catch(function (err) { showError(err.message); });
    }

    function removeSpeaker(id) {
        fetch("/api/speakers/" + encodeURIComponent(id), { method: "DELETE" })
            .then(function (r) {
                if (!r.ok && r.status !== 204) throw new Error("Failed to remove speaker");
                lastSpeakersJson = "";
                fetchStatus();
            })
            .catch(function (err) { showError(err.message); });
    }

    // ---- System info ----

    function updateSystemInfo(system) {
        if (!system) return;
        document.getElementById("system-version").textContent = system.version || "--";
        document.getElementById("system-uptime").textContent = system.uptime || "--";
    }

    // ---- Status polling ----

    function fetchStatus() {
        fetch("/api/status")
            .then(function (response) {
                if (!response.ok) throw new Error("HTTP " + response.status);
                return response.json();
            })
            .then(function (data) {
                var auth = data.auth || {};

                if (auth.authenticated) {
                    var displayName = auth.name || auth.email || "User " + auth.user_id;
                    document.getElementById("auth-name").textContent = displayName;
                    document.getElementById("auth-email").textContent = auth.email && auth.name ? auth.email : "";
                    var avatarEl = document.getElementById("auth-avatar");
                    if (auth.avatar) {
                        avatarEl.src = auth.avatar;
                        avatarEl.style.display = "";
                    } else {
                        avatarEl.style.display = "none";
                    }
                    showAuthState("connected");
                } else {
                    var loginDiv = document.getElementById("auth-login");
                    if (loginDiv.style.display === "none") {
                        showAuthState("disconnected");
                    }
                    document.getElementById("add-speaker-btn").style.display = "none";
                }

                if (auth.authenticated && data.speakers) {
                    updateSpeakers(data.speakers);
                } else if (!auth.authenticated) {
                    document.getElementById("speakers-list").innerHTML =
                        '<p class="muted">Waiting for authentication...</p>';
                    document.getElementById("add-speaker-btn").style.display = "none";
                }

                updateSystemInfo({ version: data.version, uptime: data.uptime });
            })
            .catch(function () {});
    }

    // ---- Auth functions (unchanged from original) ----

    function startLogin() {
        window.open("https://play.qobuz.com/login", "_blank");
        showAuthState("login");
        document.getElementById("login-error").style.display = "none";
        document.getElementById("localuser-value").value = "";
    }

    function cancelLogin() {
        showAuthState("disconnected");
    }

    function parseLocalUser(raw) {
        var s = raw.trim();
        try {
            var obj = JSON.parse(s);
            if (obj && typeof obj === "object" && obj.id && obj.token) {
                return {
                    user_id: String(obj.id),
                    user_auth_token: obj.token,
                    email: obj.email || "",
                    name: obj.name || "",
                    avatar: obj.avatar || "",
                };
            }
        } catch (e) {}
        return null;
    }

    function submitToken(event) {
        event.preventDefault();
        var rawValue = document.getElementById("localuser-value").value;
        var errorEl = document.getElementById("login-error");
        var parsed = parseLocalUser(rawValue);
        if (!parsed) {
            errorEl.textContent = 'Could not parse localuser value. Make sure you copied the full value of the "localuser" key.';
            errorEl.style.display = "";
            return;
        }
        errorEl.style.display = "none";
        fetch("/api/auth/token", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                user_id: parsed.user_id,
                user_auth_token: parsed.user_auth_token,
                email: parsed.email,
                name: parsed.name,
                avatar: parsed.avatar,
            }),
        })
            .then(function (response) {
                if (!response.ok) return response.json().then(function (data) { throw new Error(data.error || "Authentication failed"); });
                return response.json();
            })
            .then(function () {
                showAuthState("connected");
                fetchStatus();
            })
            .catch(function (err) {
                errorEl.textContent = err.message;
                errorEl.style.display = "";
            });
    }

    function logout() {
        fetch("/api/auth/logout", { method: "POST" })
            .then(function () {
                showAuthState("disconnected");
                document.getElementById("speakers-list").innerHTML =
                    '<p class="muted">Waiting for authentication...</p>';
                document.getElementById("add-speaker-btn").style.display = "none";
                hideAddSpeaker();
            })
            .catch(function () { showAuthState("disconnected"); });
    }

    // ---- Expose to global scope ----
    window.startLogin = startLogin;
    window.cancelLogin = cancelLogin;
    window.submitToken = submitToken;
    window.logout = logout;
    window.showAddSpeaker = showAddSpeaker;
    window.hideAddSpeaker = hideAddSpeaker;
    window.selectBackend = selectBackend;
    window.selectDLNADevice = selectDLNADevice;
    window.selectLocalDevice = selectLocalDevice;
    window.selectManualDevice = selectManualDevice;
    window.showManualEntry = showManualEntry;
    window.submitAddSpeaker = submitAddSpeaker;
    window.editSpeaker = editSpeaker;
    window.cancelEdit = cancelEdit;
    window.submitEditSpeaker = submitEditSpeaker;
    window.removeSpeaker = removeSpeaker;
    window.startDLNADiscovery = startDLNADiscovery;

    // ---- Start ----
    fetchStatus();
    pollTimer = setInterval(fetchStatus, 3000);
})();
```

### Step 7.4: Manual testing

- [ ] Run: `python3 -m qobuz_proxy` with a config that has at least one speaker
- [ ] Verify: Web UI at `http://localhost:8689` shows speaker cards with status
- [ ] Verify: "+ Add Speaker" button appears when authenticated
- [ ] Verify: Status updates every 3 seconds

### Step 7.5: Commit

```bash
git add qobuz_proxy/webui/static/
git commit -m "feat(webui): add speaker management UI with rich status, add/edit/remove flows"
```

---

## Task 8: Integration Testing and Polish

**Files:**
- Modify: `tests/webui/test_routes.py`
- Modify: `tests/webui/test_speaker_routes.py`

### Step 8.1: Verify all existing tests still pass

- [ ] Run: `pytest tests/ -v`
- [ ] Fix any breakages from the changes (most likely `make_app()` fixtures need new app keys)

### Step 8.2: Update `make_app()` in test_routes.py

- [ ] Ensure `make_app()` in `tests/webui/test_routes.py` includes the new app keys:

```python
app["on_add_speaker"] = AsyncMock()
app["on_edit_speaker"] = AsyncMock()
app["on_remove_speaker"] = AsyncMock()
app["local_audio_enabled"] = False
```

### Step 8.3: Run full test suite

- [ ] Run: `pytest tests/ -v`
- [ ] Run: `black qobuz_proxy/ tests/ --check`
- [ ] Run: `ruff check qobuz_proxy/ tests/`
- [ ] Run: `mypy qobuz_proxy/`
- [ ] Fix any issues

### Step 8.4: Commit

```bash
git add -A
git commit -m "test(webui): update test fixtures for speaker management, fix lint/type issues"
```

---

## Task 9: Update Config YAML Example

**Files:**
- Modify: `config.yaml.example`

### Step 9.1: Add `webui` section to config example

- [ ] Add a `webui` section documenting the `enable_local_audio` flag (if we decide to add it to the config schema). For now, document in comments:

```yaml
# Web UI settings
# webui:
#   enable_local_audio: false  # Show local audio backend in web UI (requires sounddevice, numpy, soundfile)
```

### Step 9.2: Commit

```bash
git add config.yaml.example
git commit -m "docs: add webui config section to config example"
```

---

## Summary of Tasks

| # | Task | Dependencies | Key Files |
|---|---|---|---|
| 1 | Speaker ID + Config serialization + YAML writer | None | config.py, config_writer.py |
| 2 | Rich speaker status | None | speaker.py, app.py |
| 3 | DLNA discovery endpoint | None | speaker_routes.py |
| 4 | Local audio discovery endpoint | Task 3 (needs speaker_routes.py) | speaker_routes.py |
| 5 | Speaker CRUD endpoints | Task 3 (needs speaker_routes.py) | speaker_routes.py |
| 6 | Runtime speaker management | Tasks 1, 2, 5 | app.py |
| 7 | Frontend UI | Tasks 2, 3, 4, 5 | HTML, CSS, JS |
| 8 | Integration testing + polish | All above | tests/ |
| 9 | Config example update | None | config.yaml.example |

**Independent tasks that can run in parallel:** Tasks 1, 2, 3 have no dependencies on each other.
