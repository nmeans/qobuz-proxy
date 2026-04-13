"""Tests for seek and position tracking (QPROXY-022)."""

import array
import asyncio
import random
from typing import Generator
from unittest.mock import AsyncMock, MagicMock, patch

from qobuz_proxy.backends.local.backend import CHUNK_SIZE, LocalAudioBackend
from qobuz_proxy.backends.types import BackendTrackMetadata, PlaybackState

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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


_SD_PATCH = "qobuz_proxy.backends.local.device._import_sounddevice"


def _make_metadata() -> BackendTrackMetadata:
    return BackendTrackMetadata(
        track_id="123",
        title="Test Track",
        artist="Test Artist",
        album="Test Album",
        duration_ms=10000,
    )


def _audio_gen(audio: array.array, channels: int, start_frame: int = 0) -> Generator:
    total_frames = len(audio) // channels
    pos = start_frame
    while pos < total_frames:
        end = min(pos + CHUNK_SIZE, total_frames)
        yield audio[pos * channels : end * channels]
        pos = end


async def _create_connected_backend() -> LocalAudioBackend:
    backend = LocalAudioBackend(device="default", buffer_size=2048)
    with patch(_SD_PATCH, return_value=_mock_sounddevice()):
        await backend.connect()
    return backend


async def _start_playback(
    backend: LocalAudioBackend,
    total_frames: int = 441000,
    sample_rate: int = 44100,
    channels: int = 2,
) -> None:
    """Start playback with fake streaming audio."""
    audio = array.array("f", [random.random() for _ in range(total_frames * channels)])

    backend._download_to_tempfile = AsyncMock(return_value="/fake/track.flac")  # type: ignore[method-assign]
    backend._get_audio_info = AsyncMock(return_value=(sample_rate, channels, total_frames))  # type: ignore[method-assign]
    backend._make_stream = lambda start_frame=0: _audio_gen(audio, channels, start_frame)  # type: ignore[method-assign]
    backend._stream.set_ring_buffer = MagicMock()
    backend._stream.open = MagicMock()
    backend._stream.start = MagicMock()
    backend._stream.stop = MagicMock()
    backend._stream.pause = MagicMock()
    backend._stream.resume = MagicMock()

    await backend.play("http://example.com/track.flac", _make_metadata())
    await asyncio.sleep(0.05)


# ---------------------------------------------------------------------------
# Tests: Position with buffer latency correction
# ---------------------------------------------------------------------------


class TestPositionBufferLatency:
    async def test_position_with_buffer_latency(self) -> None:
        backend = await _create_connected_backend()
        await _start_playback(backend, total_frames=441000, sample_rate=44100)

        backend._frames_decoded = 441000  # 10 seconds fed
        buffer_available = backend._ring_buffer.available()

        pos = await backend.get_position()
        expected_raw_ms = 441000 / 44100 * 1000
        expected_latency_ms = buffer_available / 44100 * 1000
        expected = int(expected_raw_ms - expected_latency_ms)

        assert pos == expected

        await backend.stop()
        await backend.disconnect()

    async def test_position_zero_when_stopped(self) -> None:
        backend = await _create_connected_backend()
        assert await backend.get_position() == 0
        await backend.disconnect()

    async def test_position_never_negative(self) -> None:
        backend = await _create_connected_backend()
        await _start_playback(backend, total_frames=441000, sample_rate=44100)

        backend._frames_decoded = 10  # less than what's in the buffer

        pos = await backend.get_position()
        assert pos >= 0

        await backend.stop()
        await backend.disconnect()

    async def test_position_no_buffer(self) -> None:
        backend = await _create_connected_backend()
        backend._sample_rate = 44100
        backend._frames_decoded = 44100
        backend._ring_buffer = None

        pos = await backend.get_position()
        assert pos == 1000  # 1 second, no buffer correction

        await backend.disconnect()


# ---------------------------------------------------------------------------
# Tests: Seek forward and backward
# ---------------------------------------------------------------------------


class TestSeekBasic:
    async def test_seek_forward(self) -> None:
        backend = await _create_connected_backend()
        await _start_playback(backend, total_frames=441000, sample_rate=44100)

        await backend.seek(5000)
        await asyncio.sleep(0.1)

        expected_frame = int(5000 / 1000 * 44100)
        assert backend._frames_decoded >= expected_frame

        await backend.stop()
        await backend.disconnect()

    async def test_seek_backward(self) -> None:
        backend = await _create_connected_backend()
        await _start_playback(backend, total_frames=441000, sample_rate=44100)

        await asyncio.sleep(0.1)
        await backend.seek(1000)
        await asyncio.sleep(0.1)

        expected_frame = int(1000 / 1000 * 44100)
        assert backend._frames_decoded >= expected_frame

        await backend.stop()
        await backend.disconnect()

    async def test_seek_to_zero(self) -> None:
        backend = await _create_connected_backend()
        await _start_playback(backend, total_frames=441000, sample_rate=44100)

        await asyncio.sleep(0.05)
        await backend.seek(0)
        await asyncio.sleep(0.1)

        pos = await backend.get_position()
        assert pos < 5000

        await backend.stop()
        await backend.disconnect()


# ---------------------------------------------------------------------------
# Tests: Seek edge cases
# ---------------------------------------------------------------------------


class TestSeekEdgeCases:
    async def test_seek_beyond_duration(self) -> None:
        backend = await _create_connected_backend()
        ended = []
        backend.on_track_ended(lambda: ended.append(True))

        await _start_playback(backend, total_frames=441000, sample_rate=44100)

        await backend.seek(15000)  # beyond 10 seconds
        await asyncio.sleep(0.05)

        assert len(ended) == 1
        assert backend._state == PlaybackState.STOPPED
        assert backend._frames_decoded == 441000

        await backend.disconnect()

    async def test_seek_negative_clamped(self) -> None:
        backend = await _create_connected_backend()
        await _start_playback(backend, total_frames=441000, sample_rate=44100)

        await backend.seek(-5000)
        await asyncio.sleep(0.1)

        pos = await backend.get_position()
        assert pos < 5000

        await backend.stop()
        await backend.disconnect()

    async def test_seek_no_op_without_audio(self) -> None:
        backend = await _create_connected_backend()
        await backend.seek(5000)
        assert backend._seek_target is None
        await backend.disconnect()

    async def test_seek_no_op_without_sample_rate(self) -> None:
        backend = await _create_connected_backend()
        assert backend._sample_rate == 0
        await backend.seek(5000)
        assert backend._seek_target is None
        await backend.disconnect()


# ---------------------------------------------------------------------------
# Tests: Seek while paused
# ---------------------------------------------------------------------------


class TestSeekWhilePaused:
    async def test_seek_while_paused_stays_paused(self) -> None:
        backend = await _create_connected_backend()
        await _start_playback(backend, total_frames=441000, sample_rate=44100)

        await backend.pause()
        assert backend._state == PlaybackState.PAUSED

        await backend.seek(5000)
        await asyncio.sleep(0.1)

        assert backend._state == PlaybackState.PAUSED

        await backend.stop()
        await backend.disconnect()

    async def test_seek_while_paused_updates_position(self) -> None:
        backend = await _create_connected_backend()
        await _start_playback(backend, total_frames=441000, sample_rate=44100)

        await backend.pause()
        await backend.seek(5000)
        await asyncio.sleep(0.1)

        pos = await backend.get_position()
        assert pos >= 4000

        await backend.stop()
        await backend.disconnect()


# ---------------------------------------------------------------------------
# Tests: Position updates during feeding
# ---------------------------------------------------------------------------


class TestPositionUpdates:
    async def test_position_callbacks_called(self) -> None:
        backend = await _create_connected_backend()
        positions: list[int] = []
        backend.on_position_update(lambda p: positions.append(p))

        await _start_playback(backend, total_frames=44100, sample_rate=44100)
        await asyncio.sleep(0.1)

        assert len(positions) > 0

        await backend.stop()
        await backend.disconnect()

    async def test_position_updates_are_buffer_corrected(self) -> None:
        backend = await _create_connected_backend()
        positions: list[int] = []
        backend.on_position_update(lambda p: positions.append(p))

        await _start_playback(backend, total_frames=44100, sample_rate=44100)
        await asyncio.sleep(0.1)

        for pos in positions:
            assert pos <= 1000

        await backend.stop()
        await backend.disconnect()

    async def test_seek_triggers_immediate_position_update(self) -> None:
        backend = await _create_connected_backend()
        positions: list[int] = []
        backend.on_position_update(lambda p: positions.append(p))

        await _start_playback(backend, total_frames=441000, sample_rate=44100)
        await asyncio.sleep(0.05)

        positions.clear()
        await backend.seek(5000)
        await asyncio.sleep(0.05)

        assert len(positions) > 0
        assert any(4000 <= p <= 6000 for p in positions)

        await backend.stop()
        await backend.disconnect()
