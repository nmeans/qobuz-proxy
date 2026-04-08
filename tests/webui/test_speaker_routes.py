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
        mock_device = AsyncMock()
        mock_device.friendly_name = "Sonos One"
        mock_device.ip = "192.168.1.50"
        mock_device.port = 1400
        mock_device.model_name = "Sonos One"
        mock_device.manufacturer = "Sonos, Inc."
        mock_device.udn = "uuid:123"
        mock_device.location = "http://192.168.1.50:1400/xml/desc.xml"

        with patch(
            "qobuz_proxy.webui.speaker_routes.discover_dlna_devices",
            new_callable=AsyncMock,
            return_value=[mock_device],
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
