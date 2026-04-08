"""Speaker management API routes: CRUD and discovery."""

import logging
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
