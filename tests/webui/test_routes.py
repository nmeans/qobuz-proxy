"""Tests for webui API routes."""

import pytest
from unittest.mock import AsyncMock

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from qobuz_proxy.webui.routes import register_routes


def make_app(auth_state: dict | None = None) -> web.Application:
    app = web.Application()
    if auth_state is None:
        auth_state = {"authenticated": False, "user_id": "", "email": ""}
    app["auth_state"] = auth_state
    app["get_speakers"] = lambda: []
    app["version"] = "1.2.0"
    app["on_auth_token"] = AsyncMock(return_value=True)
    app["on_logout"] = AsyncMock()
    register_routes(app)
    return app


@pytest.fixture
async def client():
    app = make_app()
    async with TestClient(TestServer(app)) as c:
        yield c


@pytest.fixture
async def authed_client():
    app = make_app(
        auth_state={"authenticated": True, "user_id": "12345", "email": "user@example.com"}
    )
    app["get_speakers"] = lambda: [{"name": "Living Room", "backend": "dlna", "status": "playing"}]
    async with TestClient(TestServer(app)) as c:
        yield c


async def test_status_unauthenticated(client: TestClient) -> None:
    resp = await client.get("/api/status")
    assert resp.status == 200
    data = await resp.json()
    assert data["auth"]["authenticated"] is False
    assert data["auth"]["user_id"] == ""
    assert data["auth"]["email"] == ""
    assert data["speakers"] == []
    assert data["version"] == "1.2.0"
    assert "uptime" in data


async def test_status_authenticated(authed_client: TestClient) -> None:
    resp = await authed_client.get("/api/status")
    assert resp.status == 200
    data = await resp.json()
    assert data["auth"]["authenticated"] is True
    assert data["auth"]["user_id"] == "12345"
    assert data["auth"]["email"] == "user@example.com"
    assert len(data["speakers"]) == 1
    assert data["speakers"][0]["name"] == "Living Room"


async def test_auth_token_success(client: TestClient) -> None:
    resp = await client.post(
        "/api/auth/token",
        json={"user_id": "12345", "user_auth_token": "secret-token"},
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["status"] == "ok"
    client.app["on_auth_token"].assert_awaited_once_with(  # type: ignore[union-attr]
        "12345", "secret-token", {"email": "", "name": "", "avatar": ""}
    )


async def test_auth_token_failure(client: TestClient) -> None:
    client.app["on_auth_token"] = AsyncMock(return_value=False)  # type: ignore[union-attr]
    resp = await client.post(
        "/api/auth/token",
        json={"user_id": "12345", "user_auth_token": "bad-token"},
    )
    assert resp.status == 401
    data = await resp.json()
    assert data["error"] == "authentication_failed"


async def test_auth_token_missing_fields(client: TestClient) -> None:
    resp = await client.post("/api/auth/token", json={"user_id": "12345"})
    assert resp.status == 400
    data = await resp.json()
    assert "error" in data


async def test_auth_token_missing_user_id(client: TestClient) -> None:
    resp = await client.post("/api/auth/token", json={"user_auth_token": "token"})
    assert resp.status == 400


async def test_auth_token_empty_body(client: TestClient) -> None:
    resp = await client.post("/api/auth/token", json={})
    assert resp.status == 400


async def test_auth_logout(client: TestClient) -> None:
    resp = await client.post("/api/auth/logout")
    assert resp.status == 200
    data = await resp.json()
    assert data["status"] == "ok"
    client.app["on_logout"].assert_awaited_once()  # type: ignore[union-attr]


async def test_status_uptime_format(client: TestClient) -> None:
    """Uptime should be formatted as 'Xh Ym' or 'Xm'."""
    resp = await client.get("/api/status")
    data = await resp.json()
    uptime = data["uptime"]
    # Should match "Xm" or "Xh Ym" pattern
    assert "m" in uptime
