"""Tests for the local audio ring buffer."""

import array
import random
import threading

import pytest

from qobuz_proxy.backends.local.ring_buffer import RingBuffer


def _zeros(frames: int, channels: int = 2) -> array.array:
    """Return a zero-filled float32 array of frames * channels samples."""
    return array.array("f", [0.0] * (frames * channels))


def _filled(frames: int, value: float, channels: int = 2) -> array.array:
    """Return a float32 array filled with a constant value."""
    return array.array("f", [value] * (frames * channels))


def _random(frames: int, channels: int = 2) -> array.array:
    """Return a float32 array of random values in [0, 1)."""
    return array.array("f", [random.random() for _ in range(frames * channels)])


def _assert_close(a: array.array, b: array.array, places: int = 5) -> None:
    """Assert two float32 arrays are element-wise approximately equal."""
    assert len(a) == len(b), f"Length mismatch: {len(a)} != {len(b)}"
    tol = 10 ** (-places)
    for i, (x, y) in enumerate(zip(a, b)):
        assert abs(x - y) < tol, f"Element {i}: {x} != {y} (tol={tol})"


class TestRingBufferInit:
    """Test RingBuffer initialization."""

    def test_default_stereo(self) -> None:
        buf = RingBuffer(1024)
        assert buf.capacity == 1024
        assert buf.channels == 2
        assert buf.available() == 0
        assert buf.free_space() == 1024

    def test_mono(self) -> None:
        buf = RingBuffer(512, channels=1)
        assert buf.channels == 1
        assert buf.capacity == 512

    def test_fill_level_empty(self) -> None:
        buf = RingBuffer(1000)
        assert buf.fill_level() == pytest.approx(0.0)


class TestRingBufferWriteRead:
    """Test basic write and read operations."""

    def test_basic_write_read(self) -> None:
        buf = RingBuffer(1024, channels=2)
        data = _random(100, channels=2)

        written = buf.write(data)
        assert written == 100
        assert buf.available() == 100

        result = buf.read(100)
        assert len(result) == 100 * 2
        _assert_close(result, data)
        assert buf.available() == 0

    def test_mono_write_read(self) -> None:
        buf = RingBuffer(1024, channels=1)
        data = _random(50, channels=1)

        written = buf.write(data)
        assert written == 50

        result = buf.read(50)
        _assert_close(result, data)

    def test_multiple_writes_single_read(self) -> None:
        buf = RingBuffer(1024, channels=2)
        chunk1 = _filled(30, 0.5, channels=2)
        chunk2 = _filled(20, 0.8, channels=2)

        buf.write(chunk1)
        buf.write(chunk2)
        assert buf.available() == 50

        result = buf.read(50)
        _assert_close(result[: 30 * 2], chunk1)
        _assert_close(result[30 * 2 :], chunk2)

    def test_single_write_multiple_reads(self) -> None:
        buf = RingBuffer(1024, channels=2)
        data = _random(100, channels=2)
        buf.write(data)

        r1 = buf.read(40)
        r2 = buf.read(60)
        _assert_close(r1, data[: 40 * 2])
        _assert_close(r2, data[40 * 2 :])


class TestRingBufferCounters:
    """Test available, free_space, and fill_level."""

    def test_available_and_free_space(self) -> None:
        buf = RingBuffer(1000, channels=2)
        buf.write(_zeros(400, channels=2))

        assert buf.available() == 400
        assert buf.free_space() == 600

    def test_fill_level(self) -> None:
        buf = RingBuffer(1000, channels=2)
        buf.write(_zeros(500, channels=2))

        assert buf.fill_level() == pytest.approx(0.5)

    def test_fill_level_full(self) -> None:
        buf = RingBuffer(100, channels=2)
        buf.write(_zeros(100, channels=2))

        assert buf.fill_level() == pytest.approx(1.0)

    def test_counters_after_read(self) -> None:
        buf = RingBuffer(1000, channels=2)
        buf.write(_zeros(600, channels=2))
        buf.read(200)

        assert buf.available() == 400
        assert buf.free_space() == 600


class TestRingBufferWrapAround:
    """Test wrap-around behavior."""

    def test_write_wrap_around(self) -> None:
        buf = RingBuffer(100, channels=2)

        # Fill to 80%, then read 60% to advance the read pointer
        buf.write(_zeros(80, channels=2))
        buf.read(60)
        # write_pos=80, read_pos=60, available=20

        # Write 40 frames — should wrap from pos 80 to pos 20
        data = _random(40, channels=2)
        written = buf.write(data)
        assert written == 40
        assert buf.available() == 60

        # Read all — first the 20 zeros, then the 40 new frames
        result = buf.read(60)
        _assert_close(result[20 * 2 :], data)

    def test_read_wrap_around(self) -> None:
        buf = RingBuffer(100, channels=2)

        # Advance positions: write 90, read 90
        buf.write(_zeros(90, channels=2))
        buf.read(90)
        # read_pos=90, write_pos=90

        # Write 30 frames — wraps from pos 90 to pos 20
        data = _random(30, channels=2)
        buf.write(data)

        # Read wraps from pos 90 to pos 20
        result = buf.read(30)
        _assert_close(result, data)


class TestRingBufferEdgeCases:
    """Test underrun, overflow, and clear."""

    def test_underrun_zero_padding(self) -> None:
        buf = RingBuffer(1024, channels=2)
        data = _filled(50, 0.7, channels=2)
        buf.write(data)

        result = buf.read(100)
        assert len(result) == 100 * 2
        # First 50 frames have data
        _assert_close(result[: 50 * 2], data)
        # Last 50 frames are zero-padded
        assert list(result[50 * 2 :]) == [0.0] * (50 * 2)

    def test_overflow_truncation(self) -> None:
        buf = RingBuffer(100, channels=2)
        buf.write(_zeros(80, channels=2))

        # Only 20 frames of free space
        big_data = _filled(50, 1.0, channels=2)
        written = buf.write(big_data)
        assert written == 20
        assert buf.available() == 100

    def test_clear(self) -> None:
        buf = RingBuffer(1024, channels=2)
        buf.write(_random(500, channels=2))
        assert buf.available() == 500

        buf.clear()
        assert buf.available() == 0
        assert buf.free_space() == 1024
        assert buf.fill_level() == pytest.approx(0.0)

    def test_clear_then_read_returns_silence(self) -> None:
        buf = RingBuffer(100, channels=2)
        buf.write(_filled(50, 1.0, channels=2))
        buf.clear()

        result = buf.read(10)
        assert list(result) == [0.0] * (10 * 2)

    def test_empty_buffer_read(self) -> None:
        buf = RingBuffer(100, channels=2)
        result = buf.read(10)
        assert len(result) == 10 * 2
        assert list(result) == [0.0] * (10 * 2)

    def test_write_zero_frames(self) -> None:
        buf = RingBuffer(100, channels=2)
        written = buf.write(array.array("f", []))
        assert written == 0
        assert buf.available() == 0

    def test_full_buffer_write_returns_zero(self) -> None:
        buf = RingBuffer(100, channels=2)
        buf.write(_zeros(100, channels=2))
        written = buf.write(_filled(10, 1.0, channels=2))
        assert written == 0


class TestRingBufferThreadSafety:
    """Test concurrent access from multiple threads."""

    def test_concurrent_write_read(self) -> None:
        buf = RingBuffer(10000, channels=2)
        total_frames = 5000
        chunk_size = 50
        errors = []

        def writer() -> None:
            written = 0
            while written < total_frames:
                data = _filled(chunk_size, 0.5, channels=2)
                n = buf.write(data)
                written += n
                if n == 0:
                    threading.Event().wait(0.0001)  # Brief yield

        def reader() -> None:
            read = 0
            while read < total_frames:
                result = buf.read(chunk_size)
                # Count frames where any channel is non-zero
                non_zero = sum(
                    1
                    for i in range(0, len(result), 2)
                    if result[i] != 0.0 or result[i + 1] != 0.0
                )
                read += non_zero
                if non_zero == 0:
                    threading.Event().wait(0.0001)  # Brief yield

        w = threading.Thread(target=writer)
        r = threading.Thread(target=reader)
        w.start()
        r.start()
        w.join(timeout=5)
        r.join(timeout=5)

        assert not w.is_alive(), "Writer thread timed out"
        assert not r.is_alive(), "Reader thread timed out"
        assert not errors
