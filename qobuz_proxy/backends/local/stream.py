"""
Audio output stream wrapper.

Manages a sounddevice OutputStream that reads from a RingBuffer
and outputs to a PortAudio device.
"""

import array
import logging
import threading
from typing import Any

from .ring_buffer import RingBuffer

logger = logging.getLogger(__name__)


class AudioOutputStream:
    """
    Wraps sounddevice.OutputStream with ring buffer integration.

    The audio callback reads from the ring buffer, applies volume,
    and outputs silence when paused.
    """

    def __init__(
        self,
        device_index: int,
        ring_buffer: RingBuffer,
        blocksize: int = 2048,
    ):
        self._device_index = device_index
        self._ring_buffer = ring_buffer
        self._blocksize = blocksize
        self._stream = None  # sd.OutputStream
        self._sample_rate: int = 0
        self._channels: int = 2
        self._volume: float = 0.5  # 0.0 to 1.0
        self._paused = False
        self._underrun_count = 0
        self._lock = threading.Lock()

    def open(self, sample_rate: int, channels: int = 2) -> None:
        """
        Open the audio stream.

        Always closes and recreates the stream so PortAudio starts fresh
        for each track — reusing a stopped stream causes the callback to
        never pull from the ring buffer.
        """
        import sounddevice as sd

        if self._stream is not None:
            self.close()

        self._sample_rate = sample_rate
        self._channels = channels
        self._underrun_count = 0

        self._stream = sd.OutputStream(
            device=self._device_index,
            samplerate=sample_rate,
            channels=channels,
            dtype="float32",
            blocksize=self._blocksize,
            callback=self._audio_callback,
        )
        logger.debug(
            f"Audio stream opened: {sample_rate}Hz, {channels}ch, blocksize={self._blocksize}"
        )

    def start(self) -> None:
        """Start the audio stream."""
        if self._stream:
            self._stream.start()

    def stop(self) -> None:
        """Stop the audio stream."""
        if self._stream:
            self._stream.stop()

    def close(self) -> None:
        """Close and release the audio stream."""
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as e:
                logger.warning(f"Error closing audio stream: {e}")
            self._stream = None
            self._sample_rate = 0

    def pause(self) -> None:
        """Pause output (callback outputs silence)."""
        self._paused = True

    def resume(self) -> None:
        """Resume output from ring buffer."""
        self._paused = False

    def set_volume(self, level: int) -> None:
        """Set volume level (0-100)."""
        self._volume = max(0.0, min(1.0, level / 100.0))

    def get_volume(self) -> int:
        """Get volume level (0-100)."""
        return int(self._volume * 100)

    def set_ring_buffer(self, ring_buffer: RingBuffer) -> None:
        """Replace the ring buffer (used when sample rate/channels change per-track)."""
        self._ring_buffer = ring_buffer

    @property
    def sample_rate(self) -> int:
        """Current stream sample rate."""
        return self._sample_rate

    @property
    def is_open(self) -> bool:
        """Check if stream is open."""
        return self._stream is not None

    def _audio_callback(self, outdata: Any, frames: int, time_info: Any, status: Any) -> None:
        """
        PortAudio callback — called from audio thread.

        Reads from ring buffer, applies volume, outputs silence when paused.
        outdata is a sounddevice-provided C-contiguous float32 buffer (frames, channels).
        """
        if status:
            logger.warning(f"Audio callback status: {status}")

        # Cast to a flat byte view so we can write without importing numpy
        n_bytes = frames * self._channels * 4  # 4 bytes per float32 sample
        mv_out = memoryview(outdata).cast("B")

        if self._paused:
            mv_out[:] = bytes(n_bytes)
            return

        data = self._ring_buffer.read(frames)

        # Check for underrun
        if self._ring_buffer.available() == 0 and not self._paused:
            self._underrun_count += 1
            if self._underrun_count % 10 == 1:
                logger.warning(f"Audio buffer underrun (count: {self._underrun_count})")

        # Apply volume and write to output buffer
        if self._volume < 1.0:
            v = self._volume
            scaled = array.array("f", (x * v for x in data))
            mv_out[:] = scaled.tobytes()
        else:
            mv_out[:] = data.tobytes()
