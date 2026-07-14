"""Bounded TCP framing for Growatt records.

The existing wire contract declares record bytes after the six-byte prefix.
Protocols 05 and 06 append a two-byte CRC outside that declared length.
"""


SUPPORTED_PROTOCOLS = frozenset((2, 5, 6))


class FrameError(ValueError):
    pass


def _emit_frames_before_error(frames, terminal_error):
    """Expose completed frames, then surface a later terminal parse error."""
    yield from frames
    raise terminal_error


class FrameBuffer:
    def __init__(self, max_frame_size=65535, max_buffer_size=None):
        if max_frame_size < 8:
            raise ValueError("max_frame_size must allow the eight-byte Growatt header")
        self.max_frame_size = max_frame_size
        self.max_buffer_size = max_buffer_size or max_frame_size
        self._buffer = bytearray()

    @property
    def pending(self):
        return bytes(self._buffer)

    def feed(self, chunk):
        """Return complete frames before surfacing any later frame error."""
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise TypeError("Growatt transport chunks must be bytes-like")

        self._buffer.extend(chunk)
        frames = []

        try:
            while len(self._buffer) >= 6:
                protocol = self._buffer[3]
                if protocol not in SUPPORTED_PROTOCOLS:
                    raise FrameError(f"unsupported Growatt protocol {protocol:02x}")

                declared_length = int.from_bytes(self._buffer[4:6], "big")
                if declared_length < 2:
                    raise FrameError(
                        "declared Growatt frame length is smaller than the record type"
                    )

                crc_length = 2 if protocol in (5, 6) else 0
                frame_length = 6 + declared_length + crc_length
                if frame_length > self.max_frame_size:
                    raise FrameError(
                        f"Growatt frame length {frame_length} exceeds maximum "
                        f"{self.max_frame_size}"
                    )

                if len(self._buffer) < frame_length:
                    break

                frames.append(bytes(self._buffer[:frame_length]))
                del self._buffer[:frame_length]

            if len(self._buffer) > self.max_buffer_size:
                raise FrameError(
                    f"incomplete frame buffer exceeds maximum {self.max_buffer_size}"
                )
        except FrameError as error:
            if not frames:
                raise
            return _emit_frames_before_error(frames, error.with_traceback(None))

        return frames
