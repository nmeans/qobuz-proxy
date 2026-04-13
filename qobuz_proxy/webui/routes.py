"""Web UI API routes for status and authentication."""

import logging
import time
from pathlib import Path
from typing import Any

from aiohttp import web

from qobuz_proxy.auth.oauth import build_oauth_url, exchange_code, extract_code
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


async def _handle_auth_login(request: web.Request) -> web.Response:
    """Redirect the user to the Qobuz OAuth sign-in page."""
    origin = request.query.get("origin", "")
    if not origin:
        return web.json_response({"error": "missing origin parameter"}, status=400)
    redirect_url = f"{origin}/auth/callback"
    oauth_url = build_oauth_url(redirect_url)
    raise web.HTTPFound(oauth_url)


async def _handle_auth_callback(request: web.Request) -> web.Response:
    """Handle the OAuth redirect from Qobuz, exchange code, and authenticate."""
    try:
        code = extract_code(str(request.url))
    except ValueError:
        raise web.HTTPFound("/?error=missing_code")

    web_app_id: str = request.app.get("get_scraped_app_id", lambda: "")()
    try:
        creds = await exchange_code(code, web_app_id=web_app_id)
    except Exception:
        logger.exception("OAuth code exchange failed")
        raise web.HTTPFound("/?error=exchange_failed")

    profile = {
        "email": creds.get("email", ""),
        "name": creds.get("display_name", ""),
        "avatar": creds.get("avatar", ""),
        "token_app_id": creds.get("token_app_id", ""),
    }

    callback = request.app["on_auth_token"]
    success: bool = await callback(
        creds["user_id"], creds["user_auth_token"], profile, validated=True
    )

    if not success:
        raise web.HTTPFound("/?error=auth_failed")

    raise web.HTTPFound("/")


async def _handle_email_login(request: web.Request) -> web.Response:
    """Authenticate with Qobuz email and password."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)

    email = (body.get("email") or "").strip()
    password = body.get("password") or ""
    if not email or not password:
        return web.json_response({"error": "email and password required"}, status=400)

    callback = request.app["on_email_login"]
    success: bool = await callback(email, password)
    if not success:
        return web.json_response({"error": "Invalid email or password"}, status=401)

    return web.json_response({"status": "ok"})


async def _handle_token_login(request: web.Request) -> web.Response:
    """Authenticate with a user_id + user_auth_token pasted from the web player."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)

    user_id = (body.get("user_id") or "").strip()
    token = (body.get("user_auth_token") or "").strip()
    if not user_id or not token:
        return web.json_response({"error": "user_id and user_auth_token required"}, status=400)

    callback = request.app["on_auth_token"]
    success: bool = await callback(user_id, token, validated=False)
    if not success:
        return web.json_response({"error": "Token validation failed — check user_id and token"}, status=401)

    return web.json_response({"status": "ok"})


async def _handle_logout(request: web.Request) -> web.Response:
    """Clear auth token via callback."""
    callback = request.app["on_logout"]
    await callback()
    return web.json_response({"status": "ok"})


def register_routes(app: web.Application) -> None:
    """Register all web UI routes on the given application."""
    app.router.add_get("/", _handle_index)
    app.router.add_get("/api/status", _handle_status)
    app.router.add_post("/api/auth/login", _handle_email_login)
    app.router.add_post("/api/auth/token", _handle_token_login)
    app.router.add_get("/auth/login", _handle_auth_login)
    app.router.add_get("/auth/callback", _handle_auth_callback)
    app.router.add_post("/api/auth/logout", _handle_logout)
    register_speaker_routes(app)
    app.router.add_static("/static", _STATIC_DIR)
