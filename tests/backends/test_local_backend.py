"""Tests for LocalAudioBackend playback (QPROXY-021)."""

import array
import asyncio
import random
from typing import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from qobuz_proxy.backends.local.backend import CHUNK_SIZE, LocalAudioBackend
from qobuz_proxy.backends.types import BackendTrackMetadata, PlaybackState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_audio(frames: int, channels: int = 2) -> array.array:
    return array.array("f", [random.random() for _ in range(frames * channels)])


# 1-second test audio
FAKE_AUDIO_44100 = _make_audio(44100, channels=2)
FAKE_AUDIO_96000 = _make_audio(96000, channels=2)


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
        duration_ms=1000,
    )


def _audio_gen(audio: array.array, channels: int, start_frame: int = 0) -> Generator:
    """Yield CHUNK_SIZE-frame chunks from *audio* starting at *start_frame*."""
    total_frames = len(audio) // channels
    pos = start_frame
    while pos < total_frames:
        end = min(pos + CHUNK_SIZE, total_frames)
        yield audio[pos * channels : end * channels]
        pos = end


def _setup_streaming_mocks(
    backend: LocalAudioBackend,
    audio: array.array,
    sample_rate: int = 44100,
    channels: int = 2,
) -> None:
    """Patch the three streaming-pipeline methods so tests need no real files."""
    total_frames = len(audio) // channels
    backend._download_to_tempfile = AsyncMock(return_value="/fake/track.flac")  # type: ignore[method-assign]
    backend._get_audio_info = AsyncMock(return_value=(sample_rate, channels, total_frames))  # type: ignore[method-assign]
    backend._make_stream = lambda start_frame=0: _audio_gen(audio, channels, start_frame)  # type: ignore[method-assign]
    backend._stream.set_ring_buffer = MagicMock()
    backend._stream.open = MagicMock()
    backend._stream.start = MagicMock()


async def _create_connected_backend() -> LocalAudioBackend:
    backend = LocalAudioBackend(device="default", buffer_size=2048)
    with patch(_SD_PATCH, return_value=_mock_sounddevice()):
        await backend.connect()
    return backend


# ---------------------------------------------------------------------------
# Tests: State Transitions
# ---------------------------------------------------------------------------


class TestPlayStateTransitions:
    async def test_play_transitions_loading_then_playing(self) -> None:
        backend = await _create_connected_backend()
        states: list[PlaybackState] = []
        backend.on_state_change(lambda s: states.append(s))

        _setup_streaming_mocks(backend, FAKE_AUDIO_44100)

        await backend.play("http://example.com/track.flac", _make_metadata())
        await asyncio.sleep(0.01)

        assert PlaybackState.LOADING in states
        assert PlaybackState.PLAYING in states
        assert states.index(PlaybackState.LOADING) < states.index(PlaybackState.PLAYING)

        await backend.stop()
        await backend.disconnect()

    async def test_play_download_failure(self) -> None:
        backend = await _create_connected_backend()
        states: list[PlaybackState] = []
        errors: list[str] = []
        backend.on_state_change(lambda s: states.append(s))
        backend.on_playback_error(lambda e: errors.append(e))

        backend._download_to_tempfile = AsyncMock(  # type: ignore[method-assign]
            side_effect=aiohttp.ClientError("Download failed")
        )

        await backend.play("http://example.com/track.flac", _make_metadata())

        assert PlaybackState.LOADING in states
        assert PlaybackState.ERROR in states
        assert len(errors) == 1
        assert "Download failed" in errors[0]

        await backend.disconnect()

    async def test_play_decode_failure(self) -> None:
        backend = await _create_connected_backend()
        errors: list[str] = []
        backend.on_playback_error(lambda e: errors.append(e))

        backend._download_to_tempfile = AsyncMock(return_value="/fake/track.flac")  # type: ignore[method-assign]
        backend._get_audio_info = AsyncMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("Decode error: unsupported format")
        )

        await backend.play("http://example.com/track.flac", _make_metadata())

        assert backend._state == PlaybackState.ERROR
        assert len(errors) == 1
        assert "Decode error" in errors[0]

        await backend.disconnect()


# ---------------------------------------------------------------------------
# Tests: Pause / Resume
# ---------------------------------------------------------------------------


class TestPauseResume:
    async def test_pause_resume(self) -> None:
        backend = await _create_connected_backend()
        _setup_streaming_mocks(backend, FAKE_AUDIO_44100)
        backend._stream.pause = MagicMock()
        backend._stream.resume = MagicMock()

        await backend.play("http://example.com/track.flac", _make_metadata())
        await asyncio.sleep(0.01)

        await backend.pause()
        assert backend._state == PlaybackState.PAUSED
        backend._stream.pause.assert_called_once()

        await backend.resume()
        assert backend._state == PlaybackState.PLAYING
        backend._stream.resume.assert_called_once()

        await backend.stop()
        await backend.disconnect()


# ---------------------------------------------------------------------------
# Tests: Stop
# ---------------------------------------------------------------------------


class TestStop:
    async def test_stop_during_playback(self) -> None:
        backend = await _create_connected_backend()
        _setup_streaming_mocks(backend, _make_audio(441000, channels=2))
        backend._stream.stop = MagicMock()

        await backend.play("http://example.com/track.flac", _make_metadata())
        await asyncio.sleep(0.01)

        assert backend._feeding_task is not None
        assert not backend._feeding_task.done()

        await backend.stop()

        assert backend._state == PlaybackState.STOPPED
        assert backend._feeding_task is None
        assert backend._tmp_path is None
        assert backend._frames_decoded == 0
        backend._stream.stop.assert_called_once()

        await backend.disconnect()


# ---------------------------------------------------------------------------
# Tests: Track End
# ---------------------------------------------------------------------------


class TestTrackEnd:
    async def test_track_ends_naturally(self) -> None:
        backend = await _create_connected_backend()
        ended = []
        backend.on_track_ended(lambda: ended.append(True))

        small_audio = _make_audio(100, channels=2)
        _setup_streaming_mocks(backend, small_audio)

        await backend.play("http://example.com/track.flac", _make_metadata())

        # 100 frames fit in the ring buffer instantly; manually drain so the
        # feeding loop's drain-wait completes.
        await asyncio.sleep(0.05)
        if backend._ring_buffer:
            backend._ring_buffer.read(backend._ring_buffer.available())
        await asyncio.sleep(0.2)

        assert len(ended) == 1
        assert backend._state == PlaybackState.STOPPED

        await backend.disconnect()


# ---------------------------------------------------------------------------
# Tests: Sample Rate Change
# ---------------------------------------------------------------------------


class TestSampleRateChange:
    async def test_stream_reopened_on_sample_rate_change(self) -> None:
        backend = await _create_connected_backend()
        open_calls: list[tuple[int, int]] = []

        def track_open(sample_rate: int, channels: int = 2) -> None:
            open_calls.append((sample_rate, channels))

        backend._stream.set_ring_buffer = MagicMock()
        backend._stream.open = track_open
        backend._stream.start = MagicMock()
        backend._stream.stop = MagicMock()

        _setup_streaming_mocks(backend, FAKE_AUDIO_44100, sample_rate=44100)
        backend._stream.open = track_open  # re-set after _setup_streaming_mocks
        await backend.play("http://example.com/track1.flac", _make_metadata())
        await asyncio.sleep(0.01)
        await backend.stop()

        _setup_streaming_mocks(backend, FAKE_AUDIO_96000, sample_rate=96000)
        backend._stream.open = track_open
        await backend.play("http://example.com/track2.flac", _make_metadata())
        await asyncio.sleep(0.01)

        assert len(open_calls) == 2
        assert open_calls[0][0] == 44100
        assert open_calls[1][0] == 96000

        await backend.stop()
        await backend.disconnect()


# ---------------------------------------------------------------------------
# Tests: Volume
# ---------------------------------------------------------------------------


class TestVolume:
    async def test_volume_delegated_to_stream(self) -> None:
        backend = await _create_connected_backend()
        backend._stream.set_volume = MagicMock()

        await backend.set_volume(75)
        assert await backend.get_volume() == 75
        backend._stream.set_volume.assert_called_with(75)

        await backend.disconnect()

    async def test_volume_clamped(self) -> None:
        backend = await _create_connected_backend()
        backend._stream.set_volume = MagicMock()

        await backend.set_volume(150)
        assert await backend.get_volume() == 100

        await backend.set_volume(-10)
        assert await backend.get_volume() == 0

        await backend.disconnect()

    async def test_volume_before_connect(self) -> None:
        backend = LocalAudioBackend()
        await backend.set_volume(80)
        assert await backend.get_volume() == 80


# ---------------------------------------------------------------------------
# Tests: Seek
# ---------------------------------------------------------------------------


class TestSeek:
    async def test_seek_sets_target(self) -> None:
        backend = await _create_connected_backend()
        backend._sample_rate = 44100
        backend._tmp_path = "/fake/track.flac"
        backend._total_frames = 441000
        backend._channels = 2

        await backend.seek(5000)  # 5 seconds
        assert backend._seek_target == int(5000 / 1000 * 44100)

        await backend.disconnect()

    async def test_seek_no_op_without_sample_rate(self) -> None:
        backend = await _create_connected_backend()
        assert backend._sample_rate == 0

        await backend.seek(5000)
        assert backend._seek_target is None

        await backend.disconnect()

    async def test_seek_no_op_without_tmp_file(self) -> None:
        backend = await _create_connected_backend()
        backend._sample_rate = 44100
        # _tmp_path is None by default

        await backend.seek(5000)
        assert backend._seek_target is None

        await backend.disconnect()


# ---------------------------------------------------------------------------
# Tests: Position
# ---------------------------------------------------------------------------


class TestPosition:
    async def test_position_zero_initially(self) -> None:
        backend = await _create_connected_backend()
        assert await backend.get_position() == 0
        await backend.disconnect()

    async def test_position_reflects_frames_decoded(self) -> None:
        backend = await _create_connected_backend()
        backend._sample_rate = 44100
        backend._frames_decoded = 44100  # 1 second

        pos = await backend.get_position()
        assert pos == 1000  # 1000 ms

        await backend.disconnect()


# ---------------------------------------------------------------------------
# Tests: Connect / Disconnect
# ---------------------------------------------------------------------------


class TestConnectDisconnect:
    async def test_connect_creates_stream(self) -> None:
        backend = LocalAudioBackend()
        with patch(_SD_PATCH, return_value=_mock_sounddevice()):
            result = await backend.connect()

        assert result is True
        assert backend.is_connected()
        assert backend._stream is not None
        assert "Test Output" in backend.name

        await backend.disconnect()

    async def test_connect_failure(self) -> None:
        sd = _mock_sounddevice()
        sd.query_devices.return_value = []

        backend = LocalAudioBackend()
        with patch(_SD_PATCH, return_value=sd):
            result = await backend.connect()

        assert result is False
        assert not backend.is_connected()

    async def test_disconnect_stops_playback(self) -> None:
        backend = await _create_connected_backend()
        _setup_streaming_mocks(backend, _make_audio(441000, channels=2))
        backend._stream.stop = MagicMock()
        backend._stream.close = MagicMock()

        await backend.play("http://example.com/track.flac", _make_metadata())
        await asyncio.sleep(0.01)

        await backend.disconnect()

        assert not backend.is_connected()
        assert backend._stream is None

    async def test_get_info(self) -> None:
        backend = LocalAudioBackend(device="usb-dac")
        info = backend.get_info()
        assert info.backend_type == "local"
        assert info.device_id == "local-usb-dac"


# ---------------------------------------------------------------------------
# Tests: Download to temp file
# ---------------------------------------------------------------------------


class TestDownloadToTempfile:
    async def test_download_streams_to_disk(self) -> None:
        """_download_to_tempfile writes response chunks to a file."""
        import os

        backend = LocalAudioBackend()
        fake_data = b"fake-flac-data-" * 100

        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.status = 200
        mock_response.headers = {"content-length": str(len(fake_data))}
        mock_response.content.iter_chunked = MagicMock(
            return_value=_async_iter([fake_data[:512], fake_data[512:]])
        )
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            path = await backend._download_to_tempfile("http://example.com/track.flac")

        try:
            assert os.path.exists(path)
            assert path.endswith(".flac")
            with open(path, "rb") as f:
                written = f.read()
            assert written == fake_data
        finally:
            if os.path.exists(path):
                os.unlink(path)

    async def test_download_cleans_up_on_error(self) -> None:
        """Temp file is deleted if download raises."""
        import os

        backend = LocalAudioBackend()

        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock(
            side_effect=aiohttp.ClientResponseError(
                request_info=MagicMock(), history=(), status=404, message="Not Found"
            )
        )
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        created_path = None

        original_mkstemp = __import__("tempfile").mkstemp

        def capturing_mkstemp(*args, **kwargs):
            nonlocal created_path
            fd, path = original_mkstemp(*args, **kwargs)
            created_path = path
            return fd, path

        with (
            patch("aiohttp.ClientSession", return_value=mock_session),
            patch("tempfile.mkstemp", side_effect=capturing_mkstemp),
        ):
            with pytest.raises(aiohttp.ClientResponseError):
                await backend._download_to_tempfile("http://example.com/track.flac")

        # Temp file should have been cleaned up
        if created_path:
            assert not os.path.exists(created_path)


# ---------------------------------------------------------------------------
# Tests: Feeding Loop
# ---------------------------------------------------------------------------


class TestFeedingLoop:
    async def test_feeding_loop_feeds_all_frames(self) -> None:
        backend = await _create_connected_backend()
        audio = _make_audio(1000, channels=2)
        _setup_streaming_mocks(backend, audio)

        positions: list[int] = []
        backend.on_position_update(lambda p: positions.append(p))

        await backend.play("http://example.com/track.flac", _make_metadata())
        await asyncio.sleep(0.1)

        assert backend._frames_decoded == 1000
        assert len(positions) > 0
        for pos in positions:
            assert pos >= 0

        await backend.stop()
        await backend.disconnect()

    async def test_feeding_loop_seek(self) -> None:
        backend = await _create_connected_backend()
        audio = _make_audio(441000, channels=2)
        _setup_streaming_mocks(backend, audio)

        await backend.play("http://example.com/track.flac", _make_metadata())
        await asyncio.sleep(0.05)

        await backend.seek(5000)
        await asyncio.sleep(0.1)

        assert backend._frames_decoded >= int(5000 / 1000 * 44100)

        await backend.stop()
        await backend.disconnect()


# ---------------------------------------------------------------------------
# Async iteration helper
# ---------------------------------------------------------------------------


async def _async_iter(items):
    for item in items:
        yield item
