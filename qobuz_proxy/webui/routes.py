"""Web UI API routes for status and authentication."""

import logging
import time
from pathlib import Path
from typing import Any

from aiohttp import web
from qobuz_proxy.webui.speaker_routes import register_speaker_routes

logger = logging.getLogger(__name__)

_start_time = time.monotonic()

_STATIC_DIR = Path(__file__).parent / "static"


def _format_uptime(seconds: float) -> str:
    """Format uptime as 'Xh Ym' or 'Xm'."""
    minutes = int(seconds // 60)
    hours = minutes // 60
    remaining_minutes = minutes % 60
    if hours > 0:
        return f"{hours}h {remaining_minutes}m"
    return f"{minutes}m"


async def _handle_index(request: web.Request) -> web.Response:
    """Serve index.html with version-based cache busting."""
    html = (_STATIC_DIR / "index.html").read_text()
    html = html.replace("{{version}}", request.app.get("version", "0"))
    return web.Response(text=html, content_type="text/html")


async def _handle_status(request: web.Request) -> web.Response:
    """Return JSON with auth state, speakers, and system info."""
    app = request.app
    uptime_seconds = time.monotonic() - _start_time
    data: dict[str, Any] = {
        "auth": app["auth_state"],
        "speakers": app["get_speakers"](),
        "version": app["version"],
        "uptime": _format_uptime(uptime_seconds),
    }
    return web.json_response(data)


async def _handle_auth_token(request: web.Request) -> web.Response:
    """Accept user_id and user_auth_token, validate via callback."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400)

    user_id = body.get("user_id")
    user_auth_token = body.get("user_auth_token")

    if not user_id or not user_auth_token:
        return web.json_response({"error": "missing_fields"}, status=400)

    # Optional profile fields from localuser paste
    profile = {
        "email": body.get("email", ""),
        "name": body.get("name", ""),
        "avatar": body.get("avatar", ""),
    }

    callback = request.app["on_auth_token"]
    success: bool = await callback(user_id, user_auth_token, profile)

    if success:
        return web.json_response({"status": "ok"})
    else:
        return web.json_response({"error": "authentication_failed"}, status=401)


async def _handle_logout(request: web.Request) -> web.Response:
    """Clear auth token via callback."""
    callback = request.app["on_logout"]
    await callback()
    return web.json_response({"status": "ok"})


def register_routes(app: web.Application) -> None:
    """Register all web UI routes on the given application."""
    app.router.add_get("/", _handle_index)
    app.router.add_get("/api/status", _handle_status)
    app.router.add_post("/api/auth/token", _handle_auth_token)
    app.router.add_post("/api/auth/logout", _handle_logout)
    register_speaker_routes(app)
    app.router.add_static("/static", _STATIC_DIR)
