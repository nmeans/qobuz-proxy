"""Integration tests for QobuzProxy OAuth auth startup flow."""

from unittest.mock import AsyncMock, patch

from qobuz_proxy.app import QobuzProxy
from qobuz_proxy.config import (
    Config,
    QobuzConfig,
    ServerConfig,
    LoggingConfig,
    SpeakerConfig,
)


def _make_config(**overrides) -> Config:
    """Create a minimal Config suitable for integration tests."""
    config = Config()
    config.qobuz = QobuzConfig()
    config.backend.type = "stub"
    config.server = ServerConfig(http_port=0, bind_address="127.0.0.1")
    config.logging = LoggingConfig(level="warning")
    # Provide a stub speaker so _start_speakers has something to work with
    config.speakers = [
        SpeakerConfig(
            name="Test Speaker",
            uuid="test-uuid",
            backend_type="stub",
            http_port=0,
            bind_address="127.0.0.1",
        )
    ]
    for k, v in overrides.items():
        setattr(config, k, v)
    return config


class TestStartupWithoutToken:
    """App starts web server and enters unauthenticated state when no token is available."""

    async def test_web_server_starts(self):
        config = _make_config()
        app = QobuzProxy(config)

        with (
            patch(
                "qobuz_proxy.app.auto_fetch_credentials",
                new_callable=AsyncMock,
                return_value={"app_id": "test-id", "app_secret": "test-secret"},
            ),
            patch("qobuz_proxy.app.load_user_token", return_value=None),
        ):
            try:
                await app.start()

                assert app._web_app is not None
                assert app._web_runner is not None
                assert app._web_site is not None
            finally:
                await app.stop()

    async def test_auth_state_is_unauthenticated(self):
        config = _make_config()
        app = QobuzProxy(config)

        with (
            patch(
                "qobuz_proxy.app.auto_fetch_credentials",
                new_callable=AsyncMock,
                return_value={"app_id": "test-id", "app_secret": "test-secret"},
            ),
            patch("qobuz_proxy.app.load_user_token", return_value=None),
        ):
            try:
                await app.start()

                assert app._auth_state["authenticated"] is False
                assert app._auth_state["user_id"] == ""
            finally:
                await app.stop()

    async def test_app_does_not_crash(self):
        config = _make_config()
        app = QobuzProxy(config)

        with (
            patch(
                "qobuz_proxy.app.auto_fetch_credentials",
                new_callable=AsyncMock,
                return_value={"app_id": "test-id", "app_secret": "test-secret"},
            ),
            patch("qobuz_proxy.app.load_user_token", return_value=None),
        ):
            try:
                await app.start()
                assert app.is_running is True
            finally:
                await app.stop()

    async def test_speakers_not_started(self):
        config = _make_config()
        app = QobuzProxy(config)

        with (
            patch(
                "qobuz_proxy.app.auto_fetch_credentials",
                new_callable=AsyncMock,
                return_value={"app_id": "test-id", "app_secret": "test-secret"},
            ),
            patch("qobuz_proxy.app.load_user_token", return_value=None),
        ):
            try:
                await app.start()
                assert app._speakers == []
            finally:
                await app.stop()


class TestStartupWithCachedToken:
    """App auto-authenticates and starts speakers when a cached token is found."""

    async def test_authenticate_called_with_cached_token(self):
        config = _make_config()
        app = QobuzProxy(config)

        with (
            patch(
                "qobuz_proxy.app.auto_fetch_credentials",
                new_callable=AsyncMock,
                return_value={"app_id": "test-id", "app_secret": "test-secret"},
            ),
            patch(
                "qobuz_proxy.app.load_user_token",
                return_value={"user_id": "999", "user_auth_token": "tok"},
            ),
            patch.object(
                app, "_authenticate", new_callable=AsyncMock, return_value=True
            ) as mock_auth,
            patch.object(app, "_start_speakers", new_callable=AsyncMock),
        ):
            try:
                await app.start()

                mock_auth.assert_awaited_once_with("999", "tok")
            finally:
                await app.stop()

    async def test_start_speakers_called(self):
        config = _make_config()
        app = QobuzProxy(config)

        with (
            patch(
                "qobuz_proxy.app.auto_fetch_credentials",
                new_callable=AsyncMock,
                return_value={"app_id": "test-id", "app_secret": "test-secret"},
            ),
            patch(
                "qobuz_proxy.app.load_user_token",
                return_value={"user_id": "999", "user_auth_token": "tok"},
            ),
            patch.object(app, "_authenticate", new_callable=AsyncMock, return_value=True),
            patch.object(app, "_start_speakers", new_callable=AsyncMock) as mock_start,
        ):
            try:
                await app.start()

                mock_start.assert_awaited_once()
            finally:
                await app.stop()

    async def test_auth_state_updated(self):
        config = _make_config()
        app = QobuzProxy(config)

        with (
            patch(
                "qobuz_proxy.app.auto_fetch_credentials",
                new_callable=AsyncMock,
                return_value={"app_id": "test-id", "app_secret": "test-secret"},
            ),
            patch(
                "qobuz_proxy.app.load_user_token",
                return_value={"user_id": "999", "user_auth_token": "tok"},
            ),
            patch.object(app, "_authenticate", new_callable=AsyncMock, return_value=True),
            patch.object(app, "_start_speakers", new_callable=AsyncMock),
        ):
            try:
                await app.start()

                assert app._auth_state["authenticated"] is True
                assert app._auth_state["user_id"] == "999"
            finally:
                await app.stop()


class TestWebUIAuthCallback:
    """Web UI callback authenticates user, saves token, and starts speakers."""

    async def test_callback_triggers_authentication(self):
        config = _make_config()
        app = QobuzProxy(config)

        with (
            patch(
                "qobuz_proxy.app.auto_fetch_credentials",
                new_callable=AsyncMock,
                return_value={"app_id": "test-id", "app_secret": "test-secret"},
            ),
            patch("qobuz_proxy.app.load_user_token", return_value=None),
            patch("qobuz_proxy.app.save_user_token"),
            patch.object(
                app, "_authenticate", new_callable=AsyncMock, return_value=True
            ) as mock_auth,
            patch.object(app, "_start_speakers", new_callable=AsyncMock),
        ):
            try:
                await app.start()

                # Simulate web UI submitting a token
                result = await app._on_auth_token("999", "tok")

                assert result is True
                mock_auth.assert_awaited_once_with("999", "tok")
            finally:
                await app.stop()

    async def test_callback_saves_token(self):
        config = _make_config()
        app = QobuzProxy(config)

        with (
            patch(
                "qobuz_proxy.app.auto_fetch_credentials",
                new_callable=AsyncMock,
                return_value={"app_id": "test-id", "app_secret": "test-secret"},
            ),
            patch("qobuz_proxy.app.load_user_token", return_value=None),
            patch("qobuz_proxy.app.save_user_token") as mock_save,
            patch.object(app, "_authenticate", new_callable=AsyncMock, return_value=True),
            patch.object(app, "_start_speakers", new_callable=AsyncMock),
        ):
            try:
                await app.start()
                await app._on_auth_token("999", "tok")

                mock_save.assert_called_once_with(user_id="999", auth_token="tok", email="")
            finally:
                await app.stop()

    async def test_callback_starts_speakers(self):
        config = _make_config()
        app = QobuzProxy(config)

        with (
            patch(
                "qobuz_proxy.app.auto_fetch_credentials",
                new_callable=AsyncMock,
                return_value={"app_id": "test-id", "app_secret": "test-secret"},
            ),
            patch("qobuz_proxy.app.load_user_token", return_value=None),
            patch("qobuz_proxy.app.save_user_token"),
            patch.object(app, "_authenticate", new_callable=AsyncMock, return_value=True),
            patch.object(app, "_start_speakers", new_callable=AsyncMock) as mock_start,
        ):
            try:
                await app.start()
                await app._on_auth_token("999", "tok")

                mock_start.assert_awaited_once()
            finally:
                await app.stop()

    async def test_callback_fails_on_bad_credentials(self):
        config = _make_config()
        app = QobuzProxy(config)

        with (
            patch(
                "qobuz_proxy.app.auto_fetch_credentials",
                new_callable=AsyncMock,
                return_value={"app_id": "test-id", "app_secret": "test-secret"},
            ),
            patch("qobuz_proxy.app.load_user_token", return_value=None),
            patch("qobuz_proxy.app.save_user_token") as mock_save,
            patch.object(app, "_authenticate", new_callable=AsyncMock, return_value=False),
            patch.object(app, "_start_speakers", new_callable=AsyncMock) as mock_start,
        ):
            try:
                await app.start()

                result = await app._on_auth_token("999", "bad-tok")

                assert result is False
                mock_save.assert_not_called()
                mock_start.assert_not_called()
            finally:
                await app.stop()
