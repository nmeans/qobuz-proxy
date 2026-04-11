"""Tests for app.py / Speaker local backend integration (QPROXY-023)."""

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from qobuz_proxy.app import QobuzProxy
from qobuz_proxy.backends.local.backend import LocalAudioBackend
from qobuz_proxy.connect.types import ConnectTokens, JWTConnectToken
from qobuz_proxy.config import AUTO_QUALITY, Config, QobuzConfig, ServerConfig, SpeakerConfig
from qobuz_proxy.speaker import Speaker

_SD_PATCH = "qobuz_proxy.backends.local.device._import_sounddevice"
_DISCOVERY_PATCH = "qobuz_proxy.speaker.DiscoveryService"


def _mock_sounddevice():
    sd = MagicMock()
    sd.query_devices.return_value = [
        {
            "name": "Test Output",
            "max_output_channels": 2,
            "max_input_channels": 0,
            "default_samplerate": 44100.0,
        },
    ]
    sd.default.device = (0, 0)
    return sd


def _make_local_config(max_quality: int = 27) -> Config:
    """Create a Config with a single local speaker and a bound-to-random-port server."""
    config = Config()
    config.qobuz = QobuzConfig(
        email="test@example.com",
        auth_token="testtoken",
        user_id="99999",
    )
    config.server = ServerConfig(http_port=0, bind_address="127.0.0.1")
    config.speakers = [
        SpeakerConfig(
            name="Test Speaker",
            uuid="test-uuid",
            backend_type="local",
            max_quality=max_quality,
            http_port=0,
            bind_address="127.0.0.1",
            audio_device="default",
            audio_buffer_size=2048,
        )
    ]
    return config


def _mock_api_client():
    """Return a mock QobuzAPIClient whose login_with_token always succeeds."""
    mock_api = MagicMock()
    mock_api.login_with_token = AsyncMock(return_value=True)
    return mock_api


class TestAppLocalBackend:
    async def test_app_creates_local_backend(self) -> None:
        """App should create LocalAudioBackend when speaker backend_type is 'local'."""
        config = _make_local_config()
        app = QobuzProxy(config)

        with (
            patch(
                "qobuz_proxy.app.auto_fetch_credentials",
                new_callable=AsyncMock,
                return_value={"app_id": "test-id", "app_secret": "test-secret"},
            ),
            patch("qobuz_proxy.app.QobuzAPIClient", return_value=_mock_api_client()),
            patch(_SD_PATCH, return_value=_mock_sounddevice()),
            patch(_DISCOVERY_PATCH) as mock_disc,
        ):
            mock_disc.return_value.start = AsyncMock()
            mock_disc.return_value.stop = AsyncMock()

            try:
                await app.start()
                assert len(app._speakers) == 1
                backend = app._speakers[0]._backend
            finally:
                await app.stop()

        assert isinstance(backend, LocalAudioBackend)

    async def test_app_skips_proxy_for_local(self) -> None:
        """No audio proxy server should be created for local backend."""
        config = _make_local_config()
        app = QobuzProxy(config)

        with (
            patch(
                "qobuz_proxy.app.auto_fetch_credentials",
                new_callable=AsyncMock,
                return_value={"app_id": "test-id", "app_secret": "test-secret"},
            ),
            patch("qobuz_proxy.app.QobuzAPIClient", return_value=_mock_api_client()),
            patch(_SD_PATCH, return_value=_mock_sounddevice()),
            patch(_DISCOVERY_PATCH) as mock_disc,
        ):
            mock_disc.return_value.start = AsyncMock()
            mock_disc.return_value.stop = AsyncMock()

            try:
                await app.start()
                proxy_server = app._speakers[0]._proxy_server
            finally:
                await app.stop()

        assert proxy_server is None

    async def test_app_quality_defaults_hires_for_local(self) -> None:
        """AUTO_QUALITY with local backend should resolve to 27 (Hi-Res 192k)."""
        config = _make_local_config(max_quality=AUTO_QUALITY)
        app = QobuzProxy(config)

        with (
            patch(
                "qobuz_proxy.app.auto_fetch_credentials",
                new_callable=AsyncMock,
                return_value={"app_id": "test-id", "app_secret": "test-secret"},
            ),
            patch("qobuz_proxy.app.QobuzAPIClient", return_value=_mock_api_client()),
            patch(_SD_PATCH, return_value=_mock_sounddevice()),
            patch(_DISCOVERY_PATCH) as mock_disc,
        ):
            mock_disc.return_value.start = AsyncMock()
            mock_disc.return_value.stop = AsyncMock()

            try:
                await app.start()
                effective_quality = app._speakers[0]._effective_quality
            finally:
                await app.stop()

        assert effective_quality == 27

    async def test_app_skips_fixed_volume_for_local(self) -> None:
        """dlna_fixed_volume flag should not be applied to a local backend player."""
        config = _make_local_config()
        config.speakers[0].dlna_fixed_volume = True  # set but shouldn't be applied
        app = QobuzProxy(config)

        with (
            patch(
                "qobuz_proxy.app.auto_fetch_credentials",
                new_callable=AsyncMock,
                return_value={"app_id": "test-id", "app_secret": "test-secret"},
            ),
            patch("qobuz_proxy.app.QobuzAPIClient", return_value=_mock_api_client()),
            patch(_SD_PATCH, return_value=_mock_sounddevice()),
            patch(_DISCOVERY_PATCH) as mock_disc,
            patch("qobuz_proxy.speaker.QobuzPlayer") as mock_player_class,
        ):
            mock_disc.return_value.start = AsyncMock()
            mock_disc.return_value.stop = AsyncMock()
            mock_player_class.return_value = MagicMock()

            try:
                await app.start()
            finally:
                await app.stop()

        mock_player_class.return_value.set_fixed_volume_mode.assert_not_called()

    async def test_setup_websocket_reuses_existing_manager(self) -> None:
        """Refreshed tokens should update the existing WsManager instead of rebuilding it."""
        config = _make_local_config()
        # Build a Speaker directly so we can test _setup_websocket in isolation
        mock_api = _mock_api_client()
        speaker = Speaker(config=config.speakers[0], api_client=mock_api, app_id="test-app-id")

        # Pre-wire the required internal state (normally set in start())
        speaker._queue = MagicMock()
        speaker._player = MagicMock()
        speaker._ws_manager = MagicMock()

        tokens = ConnectTokens(
            session_id=str(uuid.uuid4()),
            ws_token=JWTConnectToken(
                jwt="refreshed_jwt_token",
                exp=9999999999,
                endpoint="wss://test.qobuz.com/ws",
            ),
        )

        with patch("qobuz_proxy.speaker.WsManager") as mock_ws_manager_class:
            await speaker._setup_websocket(tokens)

        # Existing manager should have received updated tokens
        speaker._ws_manager.set_tokens.assert_called_once_with(tokens)
        assert speaker._ws_connected_event.is_set() is True
        # A new WsManager should NOT have been created
        mock_ws_manager_class.assert_not_called()
