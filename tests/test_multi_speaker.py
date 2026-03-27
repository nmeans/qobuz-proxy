"""Tests for multi-speaker orchestration in QobuzProxy."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from qobuz_proxy.app import QobuzProxy
from qobuz_proxy.config import Config, QobuzConfig, SpeakerConfig


def _make_speaker_config(
    name: str = "Test Speaker", http_port: int = 8689, **kwargs
) -> SpeakerConfig:
    """Return a minimal SpeakerConfig for tests."""
    defaults = dict(
        name=name,
        uuid=f"uuid-{name.lower().replace(' ', '-')}",
        backend_type="dlna",
        max_quality=27,
        http_port=http_port,
        bind_address="0.0.0.0",
        dlna_ip="192.168.1.100",
        dlna_port=1400,
        dlna_fixed_volume=False,
        proxy_port=7120,
        audio_device="default",
        audio_buffer_size=2048,
    )
    defaults.update(kwargs)
    return SpeakerConfig(**defaults)


def _make_config(*speaker_configs: SpeakerConfig) -> Config:
    """Return a Config with the given speakers and minimal Qobuz credentials."""
    config = Config()
    config.qobuz = QobuzConfig(email="test@example.com", password="secret")
    config.speakers = list(speaker_configs)
    return config


class TestMultiSpeakerOrchestration:
    async def test_starts_multiple_speakers(self):
        """Two speakers → both constructors called, both start() called, app is running."""
        sc1 = _make_speaker_config("Living Room", http_port=8689)
        sc2 = _make_speaker_config("Bedroom", http_port=8690)
        config = _make_config(sc1, sc2)

        mock_speaker_instances = [MagicMock(), MagicMock()]
        for m in mock_speaker_instances:
            m.start = AsyncMock(return_value=True)
            m.stop = AsyncMock()
            m.name = "mock"

        with (
            patch(
                "qobuz_proxy.app.auto_fetch_credentials",
                new=AsyncMock(return_value={"app_id": "id", "app_secret": "secret"}),
            ),
            patch("qobuz_proxy.app.QobuzAPIClient") as MockAPIClient,
            patch("qobuz_proxy.app.Speaker", side_effect=mock_speaker_instances) as MockSpeaker,
        ):
            MockAPIClient.return_value.login = AsyncMock(return_value=True)

            app = QobuzProxy(config)
            await app.start()

            assert MockSpeaker.call_count == 2
            for instance in mock_speaker_instances:
                instance.start.assert_called_once()
            assert app.is_running

    async def test_continues_when_one_speaker_fails(self):
        """One speaker succeeds, one returns False → app still running with one speaker."""
        sc1 = _make_speaker_config("Living Room", http_port=8689)
        sc2 = _make_speaker_config("Bedroom", http_port=8690)
        config = _make_config(sc1, sc2)

        good = MagicMock()
        good.start = AsyncMock(return_value=True)
        good.stop = AsyncMock()
        good.name = "Living Room"

        bad = MagicMock()
        bad.start = AsyncMock(return_value=False)
        bad.stop = AsyncMock()
        bad.name = "Bedroom"

        with (
            patch(
                "qobuz_proxy.app.auto_fetch_credentials",
                new=AsyncMock(return_value={"app_id": "id", "app_secret": "secret"}),
            ),
            patch("qobuz_proxy.app.QobuzAPIClient") as MockAPIClient,
            patch("qobuz_proxy.app.Speaker", side_effect=[good, bad]),
        ):
            MockAPIClient.return_value.login = AsyncMock(return_value=True)

            app = QobuzProxy(config)
            await app.start()

            assert app.is_running
            assert len(app._speakers) == 1
            assert app._speakers[0] is good

    async def test_fails_when_all_speakers_fail(self):
        """All speakers return False → RuntimeError raised."""
        sc1 = _make_speaker_config("Living Room", http_port=8689)
        config = _make_config(sc1)

        bad = MagicMock()
        bad.start = AsyncMock(return_value=False)
        bad.stop = AsyncMock()
        bad.name = "Living Room"

        with (
            patch(
                "qobuz_proxy.app.auto_fetch_credentials",
                new=AsyncMock(return_value={"app_id": "id", "app_secret": "secret"}),
            ),
            patch("qobuz_proxy.app.QobuzAPIClient") as MockAPIClient,
            patch("qobuz_proxy.app.Speaker", side_effect=[bad]),
        ):
            MockAPIClient.return_value.login = AsyncMock(return_value=True)

            app = QobuzProxy(config)
            with pytest.raises(RuntimeError, match="No speakers started"):
                await app.start()

            assert not app.is_running

    async def test_stop_stops_all_speakers(self):
        """After a successful start, stop() calls stop() on all started speakers."""
        sc1 = _make_speaker_config("Living Room", http_port=8689)
        sc2 = _make_speaker_config("Bedroom", http_port=8690)
        config = _make_config(sc1, sc2)

        mock_instances = []
        for name in ("Living Room", "Bedroom"):
            m = MagicMock()
            m.start = AsyncMock(return_value=True)
            m.stop = AsyncMock()
            m.name = name
            mock_instances.append(m)

        with (
            patch(
                "qobuz_proxy.app.auto_fetch_credentials",
                new=AsyncMock(return_value={"app_id": "id", "app_secret": "secret"}),
            ),
            patch("qobuz_proxy.app.QobuzAPIClient") as MockAPIClient,
            patch("qobuz_proxy.app.Speaker", side_effect=mock_instances),
        ):
            MockAPIClient.return_value.login = AsyncMock(return_value=True)

            app = QobuzProxy(config)
            await app.start()
            await app.stop()

            for instance in mock_instances:
                instance.stop.assert_called_once()
            assert not app.is_running
