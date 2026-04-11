"""
Thread-safe ring buffer for audio samples.

Stores interleaved float32 audio samples in a flat array.array buffer.
Used by the PortAudio audio callback to read samples for output.
"""

import array
import threading


class RingBuffer:
    """
    Thread-safe circular buffer for audio samples.

    Stores flat interleaved float32 samples (frames * channels elements)
    with wrap-around handling.
    """

    def __init__(self, capacity_frames: int, channels: int = 2):
        """
        Initialize ring buffer.

        Args:
            capacity_frames: Maximum number of audio frames to store
            channels: Number of audio channels (default: 2 for stereo)
        """
        self._capacity = capacity_frames
        self._channels = channels
        self._buffer: array.array = array.array("f")
        self._buffer.frombytes(bytes(capacity_frames * channels * 4))
        self._write_pos = 0  # in frames
        self._read_pos = 0  # in frames
        self._available = 0  # in frames
        self._lock = threading.Lock()

    def write(self, data: array.array) -> int:
        """
        Write audio frames to the buffer.

        Args:
            data: flat array.array('f') of interleaved float32 samples

        Returns:
            Number of frames actually written (may be less if buffer full)
        """
        with self._lock:
            ch = self._channels
            frames = min(len(data) // ch, self._capacity - self._available)
            if frames == 0:
                return 0

            mv_buf = memoryview(self._buffer)
            mv_data = memoryview(data)
            end_pos = self._write_pos + frames

            if end_pos <= self._capacity:
                mv_buf[self._write_pos * ch : end_pos * ch] = mv_data[: frames * ch]
            else:
                first_chunk = self._capacity - self._write_pos
                mv_buf[self._write_pos * ch : self._capacity * ch] = mv_data[: first_chunk * ch]
                second_chunk = frames - first_chunk
                mv_buf[: second_chunk * ch] = mv_data[first_chunk * ch : frames * ch]

            self._write_pos = (self._write_pos + frames) % self._capacity
            self._available += frames
            return frames

    def read(self, frames: int) -> array.array:
        """
        Read audio frames from the buffer.

        Returns exactly `frames` samples worth of data. Zero-pads if buffer underrun.

        Args:
            frames: Number of frames to read

        Returns:
            flat array.array('f') of interleaved float32 samples, length = frames * channels
        """
        with self._lock:
            ch = self._channels
            output: array.array = array.array("f")
            output.frombytes(bytes(frames * ch * 4))
            actual = min(frames, self._available)

            if actual > 0:
                mv_buf = memoryview(self._buffer)
                mv_out = memoryview(output)
                end_pos = self._read_pos + actual

                if end_pos <= self._capacity:
                    mv_out[: actual * ch] = mv_buf[self._read_pos * ch : end_pos * ch]
                else:
                    first_chunk = self._capacity - self._read_pos
                    mv_out[: first_chunk * ch] = mv_buf[self._read_pos * ch : self._capacity * ch]
                    second_chunk = actual - first_chunk
                    mv_out[first_chunk * ch : actual * ch] = mv_buf[: second_chunk * ch]

                self._read_pos = (self._read_pos + actual) % self._capacity
                self._available -= actual

            return output

    def clear(self) -> None:
        """Clear all buffered data."""
        with self._lock:
            self._write_pos = 0
            self._read_pos = 0
            self._available = 0

    def available(self) -> int:
        """Number of frames available for reading."""
        with self._lock:
            return self._available

    def free_space(self) -> int:
        """Number of frames that can be written."""
        with self._lock:
            return self._capacity - self._available

    def fill_level(self) -> float:
        """Buffer fill level as ratio 0.0 to 1.0."""
        with self._lock:
            return self._available / self._capacity if self._capacity > 0 else 0.0

    @property
    def capacity(self) -> int:
        """Total buffer capacity in frames."""
        return self._capacity

    @property
    def channels(self) -> int:
        """Number of audio channels."""
        return self._channels
