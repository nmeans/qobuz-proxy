import pytest
from unittest.mock import AsyncMock, patch
from qobuz_proxy.auth.api_client import QobuzAPIClient


class TestLoginWithToken:
    async def test_successful_login(self):
        client = QobuzAPIClient("app123", "secret456")
        mock_response = {
            "user_auth_token": "fresh_token",
            "user": {"id": 999, "email": "test@example.com"},
        }
        with patch.object(client, "_request_signed", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = mock_response
            result = await client.login_with_token("999", "old_token")

        assert result is True
        assert client.user_auth_token == "fresh_token"
        assert client.user_id == "999"
        mock_req.assert_called_once_with(
            "user",
            "login",
            params={"user_id": "999", "user_auth_token": "old_token", "app_id": "app123"},
            method="POST",
            body="extra=partner",
        )

    async def test_failed_login_returns_false(self):
        client = QobuzAPIClient("app123", "secret456")
        with patch.object(client, "_request_signed", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = None
            result = await client.login_with_token("999", "bad_token")
        assert result is False
        assert client.user_auth_token is None

    async def test_login_exception_returns_false(self):
        client = QobuzAPIClient("app123", "secret456")
        with patch.object(client, "_request_signed", new_callable=AsyncMock) as mock_req:
            mock_req.side_effect = Exception("network error")
            result = await client.login_with_token("999", "token")
        assert result is False
