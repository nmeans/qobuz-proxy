"""
Local audio backend.

Downloads FLAC audio from Qobuz to a temp file, then stream-decodes and
plays through the local audio device via PortAudio.

Memory model: peak RAM is ~64 KB during download (chunk buffer) plus the
ring buffer (~3 MB for 10 s at 96 kHz).  The compressed FLAC file lives
on disk rather than in memory, which makes Hi-Res playback feasible on
low-RAM hardware such as a Raspberry Pi Zero W2.
"""

import array
import asyncio
import logging
import os
import tempfile
from typing import Any, Generator, Optional

import aiohttp

from qobuz_proxy.backends.base import AudioBackend
from qobuz_proxy.backends.types import (
    BackendInfo,
    BackendTrackMetadata,
    BufferStatus,
    PlaybackState,
)
from .device import AudioDeviceInfo, resolve_device
from .ring_buffer import RingBuffer
from .stream import AudioOutputStream

logger = logging.getLogger(__name__)

CHUNK_SIZE = 8192  # frames per decode iteration
BUFFER_SECONDS = 10  # ring buffer capacity in seconds
BUFFER_HIGH_WATER = 0.8  # pause feeding when buffer above this level


class LocalAudioBackend(AudioBackend):
    """Local audio output backend using sounddevice/PortAudio."""

    def __init__(
        self,
        device: str = "default",
        buffer_size: int = 2048,
        name: str = "Local Audio",
    ):
        super().__init__(name)
        self._device_config = device
        self._buffer_size = buffer_size

        # Device and audio components (initialized in connect())
        self._device_info: Optional[AudioDeviceInfo] = None
        self._ring_buffer: Optional[RingBuffer] = None
        self._stream: Optional[AudioOutputStream] = None

        # Playback state
        self._tmp_path: Optional[str] = None  # temp file holding compressed audio
        self._channels: int = 2
        self._sample_rate: int = 0
        self._frames_decoded: int = 0  # frames fed into the ring buffer so far
        self._total_frames: int = 0
        self._feeding_task: Optional[asyncio.Task[None]] = None

        # Download state (download runs concurrently with feeding)
        self._download_task: Optional[asyncio.Task[None]] = None
        self._download_complete: bool = False
        self._header_ready: Optional[asyncio.Event] = None

        # Seek support
        self._seek_target: Optional[int] = None

        # Buffer status tracking
        self._last_buffer_status: BufferStatus = BufferStatus.OK

    # ------------------------------------------------------------------
    # Public AudioBackend interface
    # ------------------------------------------------------------------

    async def play(self, url: str, metadata: BackendTrackMetadata) -> None:
        """Start streaming FLAC playback.

        The download runs in the background; playback begins as soon as the
        FLAC header is available (first HTTP chunk, typically < 1 second).
        The feeding loop reads the growing temp file and handles the case
        where decoding catches up to the download position.
        """
        await self._cancel_feeding()
        await self._cancel_download()
        await self._cleanup_tempfile()

        self._notify_state_change(PlaybackState.LOADING)

        try:
            # Create temp file and start downloading in background
            fd, path = tempfile.mkstemp(suffix=".flac")
            os.close(fd)  # _stream_download opens it itself
            self._tmp_path = path
            self._download_complete = False
            self._header_ready = asyncio.Event()

            self._download_task = asyncio.create_task(
                self._stream_download(url, path, self._header_ready)
            )

            # Wait only for the first chunk (contains FLAC header) — usually < 1 s
            try:
                await asyncio.wait_for(asyncio.shield(self._header_ready.wait()), timeout=15.0)
            except asyncio.TimeoutError:
                raise RuntimeError("Download stalled: no data received in 15 seconds")

            # If the download task already failed, surface the error now
            if self._download_task.done() and self._download_task.exception():
                raise self._download_task.exception()  # type: ignore[misc]

            # Read audio parameters from the FLAC header now in the temp file
            sample_rate, channels, total_frames = await self._get_audio_info(self._tmp_path)
            self._sample_rate = sample_rate
            self._channels = channels
            self._total_frames = total_frames
            self._frames_decoded = 0
            self._seek_target = None

            # Ring buffer sized for this track's sample rate
            buffer_frames = int(sample_rate * BUFFER_SECONDS)
            self._ring_buffer = RingBuffer(buffer_frames, channels)

            self._stream.set_ring_buffer(self._ring_buffer)
            self._stream.open(sample_rate, channels)
            self._stream.start()

            self._feeding_task = asyncio.create_task(self._feeding_loop())
            self._notify_state_change(PlaybackState.PLAYING)

            logger.info(
                f"Playing: {metadata.artist} - {metadata.title} "
                f"({sample_rate}Hz, {total_frames} frames)"
            )

        except Exception as e:
            logger.error(f"Playback error: {e}")
            await self._cancel_download()
            await self._cleanup_tempfile()
            self._notify_state_change(PlaybackState.ERROR)
            self._notify_playback_error(str(e))

    async def pause(self) -> None:
        if self._stream:
            self._stream.pause()
        self._notify_state_change(PlaybackState.PAUSED)

    async def resume(self) -> None:
        if self._stream:
            self._stream.resume()
        self._notify_state_change(PlaybackState.PLAYING)

    async def stop(self) -> None:
        await self._cancel_feeding()
        await self._cancel_download()
        if self._ring_buffer:
            self._ring_buffer.clear()
        if self._stream:
            self._stream.stop()
        self._frames_decoded = 0
        await self._cleanup_tempfile()
        self._notify_state_change(PlaybackState.STOPPED)

    async def seek(self, position_ms: int) -> None:
        """Seek to position in the current track."""
        if self._sample_rate == 0 or self._tmp_path is None:
            return

        target_frame = int(position_ms / 1000 * self._sample_rate)

        if target_frame >= self._total_frames:
            logger.debug(f"Seek beyond duration ({position_ms}ms), ending track")
            await self._cancel_feeding()
            if self._ring_buffer:
                self._ring_buffer.clear()
            self._frames_decoded = self._total_frames
            self._notify_state_change(PlaybackState.STOPPED)
            self._notify_track_ended()
            return

        target_frame = max(0, target_frame)
        logger.debug(f"Seek to {position_ms}ms (frame {target_frame})")
        self._seek_target = target_frame

        # If the feeding loop isn't running (e.g. paused after track end),
        # restart it so it can apply the seek.
        if self._feeding_task is None or self._feeding_task.done():
            if self._ring_buffer:
                self._ring_buffer.clear()
            self._frames_decoded = target_frame
            self._feeding_task = asyncio.create_task(self._feeding_loop())

    async def get_position(self) -> int:
        """Current playback position in ms, corrected for ring buffer latency."""
        if self._sample_rate == 0:
            return 0
        raw_ms = self._frames_decoded / self._sample_rate * 1000
        latency_ms = 0.0
        if self._ring_buffer:
            latency_ms = self._ring_buffer.available() / self._sample_rate * 1000
        return int(max(0.0, raw_ms - latency_ms))

    async def set_volume(self, level: int) -> None:
        self._volume = max(0, min(100, level))
        if self._stream:
            self._stream.set_volume(level)

    async def get_volume(self) -> int:
        return self._volume

    async def get_state(self) -> PlaybackState:
        return self._state

    async def get_buffer_status(self) -> BufferStatus:
        if not self._ring_buffer:
            return BufferStatus.OK
        return self._buffer_status_from_level(self._ring_buffer.fill_level())

    async def connect(self) -> bool:
        """Resolve audio device and create the output stream."""
        try:
            self._device_info = resolve_device(self._device_config)
            self.name = f"Local: {self._device_info.name}"
            self._stream = AudioOutputStream(
                device_index=self._device_info.index,
                ring_buffer=RingBuffer(1, 2),  # placeholder, replaced per-track
                blocksize=self._buffer_size,
            )
            self._is_connected = True
            logger.info(
                f"Audio output device: {self._device_info.name} "
                f"({int(self._device_info.default_samplerate)} Hz, "
                f"{self._device_info.channels}ch)"
            )
            return True
        except (ValueError, ImportError) as e:
            logger.error(f"Failed to initialize audio device: {e}")
            return False

    async def disconnect(self) -> None:
        await self.stop()
        if self._stream:
            self._stream.close()
            self._stream = None
        self._is_connected = False

    def get_info(self) -> BackendInfo:
        return BackendInfo(
            backend_type="local",
            name=self.name,
            device_id=f"local-{self._device_config}",
        )

    # ------------------------------------------------------------------
    # Internal helpers — download / decode pipeline
    # ------------------------------------------------------------------

    async def _stream_download(
        self, url: str, path: str, header_ready: asyncio.Event
    ) -> None:
        """Download *url* to *path* in the background.

        Sets *header_ready* after the first chunk is flushed so the caller
        can proceed with audio-info extraction while the rest downloads.
        Sets ``self._download_complete`` when the transfer finishes.
        """
        logger.debug("Downloading audio...")
        total = 0
        timeout = aiohttp.ClientTimeout(total=300, connect=15, sock_read=30)
        headers = {"User-Agent": "Qobuz/6.0.0 CFNetwork/1568.300.101 Darwin/24.2.0"}
        loop = asyncio.get_running_loop()
        last_log = loop.time()
        try:
            with open(path, "wb") as f:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(url, headers=headers) as response:
                        response.raise_for_status()
                        logger.debug(
                            f"Download started: HTTP {response.status}, "
                            f"content-length={response.headers.get('content-length', 'unknown')}"
                        )
                        async for chunk in response.content.iter_chunked(65536):
                            await asyncio.to_thread(f.write, chunk)
                            total += len(chunk)
                            if not header_ready.is_set():
                                # Flush so get_file_info() can read the header
                                await asyncio.to_thread(f.flush)
                                header_ready.set()
                            now = loop.time()
                            if now - last_log >= 5.0:
                                logger.debug(f"Downloaded {total // 1024}KB so far...")
                                last_log = now
            logger.debug(f"Download complete: {total} bytes")
            self._download_complete = True
        except asyncio.CancelledError:
            logger.debug("Download cancelled")
            raise
        except Exception as e:
            logger.error(f"Download error: {e}")
            header_ready.set()  # Unblock play() so it can surface the error
            self._download_complete = True  # Unblock feeding loop
            raise

    async def _get_audio_info(self, path: str) -> tuple[int, int, int]:
        """Return (sample_rate, channels, total_frames) for *path* (thread pool)."""
        import miniaudio
        info = await asyncio.to_thread(miniaudio.get_file_info, path)
        return info.sample_rate, info.nchannels, info.num_frames

    def _make_stream(self, start_frame: int = 0) -> Generator[array.array, Any, None]:
        """Return a miniaudio stream_file generator starting at *start_frame*.

        Synchronous — call via ``asyncio.to_thread`` for large seeks.
        ``seek_frame`` is handled natively by miniaudio (uses the FLAC seektable),
        so seeking is O(1) regardless of position.
        """
        import miniaudio
        return miniaudio.stream_file(
            self._tmp_path,
            output_format=miniaudio.SampleFormat.FLOAT32,
            nchannels=self._channels,
            sample_rate=self._sample_rate,
            frames_to_read=CHUNK_SIZE,
            seek_frame=start_frame,
        )

    # ------------------------------------------------------------------
    # Feeding loop
    # ------------------------------------------------------------------

    async def _feeding_loop(self) -> None:
        """Decode FLAC from the temp file and feed the ring buffer in chunks.

        Each ``next()`` call is dispatched to the thread pool so the event
        loop is never blocked, even briefly, during FLAC decode.
        """
        stream: Generator[array.array, Any, None] = await asyncio.to_thread(
            self._make_stream, 0
        )
        pending_chunk: Optional[array.array] = None

        try:
            while True:
                # ---- seek ------------------------------------------------
                if self._seek_target is not None:
                    target = self._seek_target
                    self._seek_target = None
                    self._ring_buffer.clear()
                    pending_chunk = None
                    if target >= self._total_frames:
                        break
                    stream = await asyncio.to_thread(self._make_stream, target)
                    self._frames_decoded = target
                    position_ms = int(target / self._sample_rate * 1000)
                    self._notify_position_update(position_ms)
                    continue

                # ---- fetch next chunk ------------------------------------
                if pending_chunk is None:
                    chunk: Optional[array.array] = await asyncio.to_thread(
                        next, stream, None  # type: ignore[arg-type]
                    )
                    if chunk is None:
                        if not self._download_complete:
                            # Decoder caught up with the download — wait and retry
                            # from the current frame position (download is still writing)
                            await asyncio.sleep(0.2)
                            stream = await asyncio.to_thread(
                                self._make_stream, self._frames_decoded
                            )
                            continue
                        break  # download finished and decoder exhausted → done
                    pending_chunk = chunk

                # ---- back-pressure: wait if ring buffer is nearly full ---
                if self._ring_buffer.fill_level() > BUFFER_HIGH_WATER:
                    await asyncio.sleep(0.05)
                    continue

                # ---- write to ring buffer --------------------------------
                written = self._ring_buffer.write(pending_chunk)
                self._frames_decoded += written
                pending_chunk = None

                self._check_buffer_status()

                buffer_latency = self._ring_buffer.available()
                actual_frames = self._frames_decoded - buffer_latency
                position_ms = int(max(0, actual_frames) / self._sample_rate * 1000)
                self._notify_position_update(position_ms)

                await asyncio.sleep(0)  # yield to event loop between chunks

            # ---- drain ring buffer before signalling end -----------------
            while self._ring_buffer.available() > 0:
                if self._state == PlaybackState.STOPPED:
                    return
                await asyncio.sleep(0.1)

            self._notify_state_change(PlaybackState.STOPPED)
            self._notify_track_ended()

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Feeding loop error: {e}")
            self._notify_state_change(PlaybackState.ERROR)
            self._notify_playback_error(str(e))
        finally:
            await self._cleanup_tempfile()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _cancel_download(self) -> None:
        if self._download_task and not self._download_task.done():
            self._download_task.cancel()
            try:
                await self._download_task
            except (asyncio.CancelledError, Exception):
                pass
        self._download_task = None
        self._download_complete = False

    async def _cancel_feeding(self) -> None:
        if self._feeding_task and not self._feeding_task.done():
            self._feeding_task.cancel()
            try:
                await self._feeding_task
            except asyncio.CancelledError:
                pass
        self._feeding_task = None

    async def _cleanup_tempfile(self) -> None:
        """Delete the temp FLAC file if it exists."""
        if self._tmp_path:
            try:
                os.unlink(self._tmp_path)
                logger.debug(f"Deleted temp file {self._tmp_path}")
            except OSError:
                pass
            self._tmp_path = None

    def _check_buffer_status(self) -> None:
        if not self._ring_buffer:
            return
        status = self._buffer_status_from_level(self._ring_buffer.fill_level())
        if status != self._last_buffer_status:
            self._last_buffer_status = status
            self._notify_buffer_status(status)
            if status == BufferStatus.EMPTY:
                logger.warning("Audio buffer underrun — audio may glitch")

    @staticmethod
    def _buffer_status_from_level(level: float) -> BufferStatus:
        if level == 0.0:
            return BufferStatus.EMPTY
        if level < 0.10:
            return BufferStatus.LOW
        if level >= 1.0:
            return BufferStatus.FULL
        return BufferStatus.OK
