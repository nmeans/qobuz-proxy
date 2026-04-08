# Web UI Speaker Configuration

**Date:** 2026-04-08
**Status:** Draft

## Overview

Add full speaker lifecycle management to the existing web UI: discover devices, add/configure speakers, edit settings, remove speakers, and view live playback status with rich metadata. All changes persist to `config.yaml`.

## Requirements

### Functional

1. **Discover DLNA devices** via SSDP scan from the UI, with rescan and manual IP entry fallback
2. **Enumerate local audio devices** (gated by config flag `enable_local_audio_ui` or env var `QOBUZPROXY_LOCAL_AUDIO_UI`)
3. **Add speakers** through a 3-step flow: choose backend type → select device → configure and add
4. **Edit speakers** inline — card expands into a form pre-filled with current settings
5. **Remove speakers** — immediate stop and removal, no confirmation dialog
6. **Live playback status** per speaker: state (playing/paused/idle/disconnected), album art, track title, artist, album, quality level, volume
7. **Persist changes** to `config.yaml` on every add/edit/remove

### Non-Functional

- No new Python dependencies (PyYAML already available; no `ruamel.yaml`)
- No new frontend dependencies (vanilla JS, consistent with existing UI)
- 3-second polling interval for status updates (existing pattern)

## API Design

### Discovery Endpoints

**`POST /api/discover/dlna`**

Triggers SSDP multicast scan. Accepts optional `timeout` parameter (default 5s).

Response:
```json
{
  "devices": [
    {
      "friendly_name": "Sonos One",
      "ip": "192.168.1.50",
      "port": 1400,
      "model_name": "Sonos One",
      "manufacturer": "Sonos, Inc.",
      "udn": "uuid:...",
      "location": "http://192.168.1.50:1400/xml/device_description.xml"
    }
  ],
  "count": 1
}
```

**`GET /api/discover/audio-devices`**

Returns list of local audio output devices. Returns 404 if local audio UI is disabled.

Response:
```json
{
  "devices": [
    {
      "name": "Built-in Output",
      "index": 0,
      "channels": 2,
      "sample_rate": 44100,
      "is_default": true
    }
  ]
}
```

### Speaker CRUD Endpoints

**`GET /api/speakers`**

Returns all speakers with config and live status. The existing `GET /api/status` endpoint is extended to include the same speaker data in its response (for backward compatibility with the polling loop), but `GET /api/speakers` is the canonical endpoint for speaker management.

Response per speaker:
```json
{
  "id": "living-room",
  "name": "Living Room",
  "backend": "dlna",
  "status": "playing",
  "config": {
    "dlna_ip": "192.168.1.50",
    "dlna_port": 1400,
    "description_url": "http://192.168.1.50:1400/xml/device_description.xml",
    "max_quality": "auto",
    "fixed_volume": false
  },
  "now_playing": {
    "title": "Windowlicker",
    "artist": "Aphex Twin",
    "album": "Windowlicker EP",
    "album_art_url": "https://...",
    "quality": "Hi-Res 96kHz",
    "volume": 42
  }
}
```

The `now_playing` field is `null` when the speaker is idle or disconnected.

**`POST /api/speakers`**

Add a new speaker. Request body:
```json
{
  "name": "Living Room",
  "backend": "dlna",
  "dlna_ip": "192.168.1.50",
  "dlna_port": 1400,
  "description_url": "http://192.168.1.50:1400/xml/device_description.xml",
  "max_quality": "auto",
  "fixed_volume": false
}
```

For local audio:
```json
{
  "name": "Bedroom",
  "backend": "local",
  "audio_device": "Built-in Output",
  "buffer_size": 2048,
  "max_quality": "auto"
}
```

Returns the created speaker (same shape as GET) with status `201`.

Validation errors return `400` with `{"error": "message"}`. Duplicate names are rejected.

**`PUT /api/speakers/{id}`**

Update speaker settings. Same body shape as POST (minus `backend` which is immutable). Triggers stop → reconfigure → restart. Returns updated speaker.

**`DELETE /api/speakers/{id}`**

Stop and remove speaker. Returns `204`.

### Speaker ID

Derived from the speaker name: lowercased, spaces replaced with hyphens, non-alphanumeric characters stripped. E.g., "Living Room" → "living-room". Must be unique.

## Config Persistence

- On every speaker add/edit/remove, the full `Config` object is serialized back to `config.yaml` using PyYAML
- Atomic write: write to a temp file in the same directory, then `os.replace()` to the target path
- The `speakers` list in YAML is the canonical representation
- Comments in the YAML file are not preserved (PyYAML limitation, documented in UI or README)

## Runtime Speaker Management

### Adding a Speaker

1. Validate config (name uniqueness, required fields, port range)
2. Auto-assign HTTP discovery and audio proxy ports (avoid conflicts with running speakers)
3. Generate deterministic UUID from speaker name
4. Create `SpeakerConfig` → `Speaker` instance
5. Start speaker (backend, mDNS, WebSocket, proxy server)
6. Add to app's speakers list
7. Persist to `config.yaml`

### Editing a Speaker

1. Validate new config
2. Stop existing speaker (full teardown: WebSocket, mDNS, backend, proxy)
3. Create new `Speaker` instance with updated config
4. Start new speaker
5. Replace in app's speakers list
6. Persist to `config.yaml`

### Removing a Speaker

1. Stop speaker (full teardown)
2. Remove from app's speakers list
3. Persist to `config.yaml`

All three operations stop playback immediately if the speaker is active. No confirmation dialogs.

## UI Design

### Page Layout

Extends the existing single-page app. Order from top to bottom:

1. **Auth section** (existing — login/status)
2. **Speakers section** (new)
3. **System info section** (existing — version/uptime)

### Speaker List

Each speaker is a card showing:

- **Playing/Paused:** Album art (80×80), track title, artist, album name, quality badge, volume level, state badge, backend type badge, Edit/Remove buttons
- **Idle:** Compact — speaker name, state badge, backend badge, device info summary, Edit/Remove buttons
- **Disconnected:** Same as idle layout, full opacity, shows IP/settings for reference

"+ Add Speaker" button at top-right of the section.

### Add Speaker Flow

Three-step inline flow (appears below the existing speaker list):

1. **Choose backend type:** Two cards — "DLNA / UPnP" and "Local Audio". Local Audio card is hidden if `enable_local_audio_ui` is false, or disabled with explanation if deps are missing.

2. **Select device:**
   - DLNA: triggers scan on render, shows list of found devices. "Rescan" button. "Or enter IP address manually" link expands a manual IP/port form.
   - Local: dropdown of enumerated audio output devices.

3. **Configure:** Form pre-filled from selected device.
   - DLNA fields: name (from `friendly_name`), IP, port, description URL (from `location`), max quality dropdown, fixed volume checkbox.
   - Local fields: name, audio device (dropdown), buffer size, max quality dropdown.
   - "Cancel" and "Add Speaker" buttons.

### Edit Speaker Flow

Clicking "Edit" expands the card inline into a form (blue border highlight):

- Pre-filled with current settings
- Backend type shown but not editable (remove + re-add to change)
- "Cancel" collapses back to normal card view
- "Save Changes" triggers the stop → reconfigure → restart cycle. Card collapses back to normal view; status briefly shows as Disconnected until the speaker reconnects

## Error Handling

| Scenario | Behavior |
|---|---|
| Discovery finds no devices | "No devices found" message + Rescan button + manual entry link |
| Speaker fails to start | Error shown inline on card; speaker saved to config, shown as Disconnected |
| Config write fails | Toast/banner error; in-memory state still updated, speaker runs |
| Local audio deps missing | Local Audio option hidden (if gated by config) or disabled with explanation |
| Duplicate speaker name | 400 error from API, validation message on name field |
| Speaker already exists at same IP | Allowed — user may want multiple speakers on same device |

## Testing Strategy

- **API endpoints:** Unit tests for each CRUD and discovery endpoint, mocking backend factory and DLNA discovery
- **Config persistence:** Tests verifying add/edit/remove serialize correctly to YAML with atomic writes
- **Runtime lifecycle:** Tests for hot add/stop/restart, verifying mDNS registration/deregistration and port assignment
- **Frontend:** Manual testing (consistent with existing UI approach)
- **Integration:** Full flow from API call → speaker start → status in `/api/status`

## Local Audio UI Gating

Local audio backend requires optional dependencies (`sounddevice`, `numpy`, `soundfile`). The UI exposes local audio configuration only when enabled:

- Config: `webui.enable_local_audio: true` in `config.yaml`
- Env var: `QOBUZPROXY_LOCAL_AUDIO_UI=true`
- Default: `false`

When disabled, the "Local Audio" card in step 1 of the Add flow is not rendered, and `GET /api/discover/audio-devices` returns 404.
