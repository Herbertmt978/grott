import hashlib
import random
import socket
import threading
from concurrent.futures import Future
from types import SimpleNamespace

import libscrc
import pytest

import grottdata
import grottproxy
from grottlayout import base_layout_name, generic_layout_name
from grottprotocol import FrameBuffer, FrameError


def make_frame(protocol=2, record_type=b"\x01\x04", payload=b"payload"):
    declared_length = len(record_type) + len(payload)
    frame = (
        b"\x00\x01\x00"
        + bytes([protocol])
        + declared_length.to_bytes(2, "big")
        + record_type
        + payload
    )
    if protocol in (5, 6):
        frame += libscrc.modbus(frame).to_bytes(2, "big")
    return frame


def make_encrypted_configure_frame(command, corrupt_crc=False):
    plaintext = bytearray(50)
    plaintext[30:32] = bytes.fromhex(command)
    mask = b"Growatt"
    encrypted = bytes(
        value ^ mask[index % len(mask)] for index, value in enumerate(plaintext)
    )
    frame = bytearray(
        make_frame(protocol=6, record_type=b"\x01\x18", payload=encrypted)
    )
    if corrupt_crc:
        frame[-1] ^= 0xFF
    return bytes(frame)


@pytest.mark.parametrize("protocol", [2, 5, 6])
def test_frame_buffer_emits_one_exact_frame_at_every_split(protocol):
    frame = make_frame(protocol=protocol)

    for split_at in range(len(frame) + 1):
        framer = FrameBuffer()
        emitted = framer.feed(frame[:split_at])
        emitted += framer.feed(frame[split_at:])

        assert emitted == [frame]
        assert framer.pending == b""


def test_frame_buffer_emits_coalesced_frames_once_in_order():
    frames = [
        make_frame(protocol=2, payload=b"first"),
        make_frame(protocol=6, payload=b"second"),
        make_frame(protocol=5, payload=b"third"),
    ]

    framer = FrameBuffer()

    assert framer.feed(b"".join(frames)) == frames
    assert framer.pending == b""


def test_frame_buffer_preserves_randomized_multi_chunk_boundaries():
    frames = [
        make_frame(protocol=2, payload=b"one"),
        make_frame(protocol=6, payload=b"two-two"),
        make_frame(protocol=5, payload=b"three-three-three"),
    ]
    stream = b"".join(frames)
    randomizer = random.Random(0x47524F5454)

    for _ in range(50):
        framer = FrameBuffer()
        emitted = []
        offset = 0
        while offset < len(stream):
            chunk_length = randomizer.randint(1, 11)
            emitted.extend(framer.feed(stream[offset : offset + chunk_length]))
            offset += chunk_length

        assert emitted == frames
        assert framer.pending == b""


def test_frame_buffer_rejects_unknown_protocol_before_deriving_length():
    frame = make_frame(protocol=9, payload=b"unknown")

    with pytest.raises(FrameError, match="unsupported Growatt protocol 09"):
        FrameBuffer().feed(frame)


def test_sanitized_captured_protocol_06_frame_survives_every_split_boundary(
    sanitized_protocol_06_capture, decrypt_protocol_06_capture
):
    frame = sanitized_protocol_06_capture
    plaintext = decrypt_protocol_06_capture(frame)
    header = frame[:8].hex()
    layout = generic_layout_name(
        base_layout_name(header, len(frame), is_smart_meter=False),
        header[12:14],
        header[14:16],
    )

    assert len(frame) == 265
    assert frame[3] == 6
    assert frame[6:8] == bytes.fromhex("0104")
    assert int.from_bytes(frame[4:6], "big") == 257
    assert frame[-2:] == bytes.fromhex("66ce")
    assert frame[-2:] == libscrc.modbus(frame[:-2]).to_bytes(2, "big")
    assert hashlib.sha256(frame).hexdigest() == (
        "b1f734d4545020a882175c038dff7e09da70fcf5221f2b46318fa8ff3bbdaca8"
    )
    assert plaintext[8:18] == b"DL00000001"
    assert plaintext[38:48] == b"INV0000001"
    assert layout == "T06NNNN"
    assert grottproxy.validate_record(frame) == 0
    assert grottproxy.validate_record(frame[:-1]) == 8

    corrupted = bytearray(frame)
    corrupted[-1] ^= 0x01
    assert grottproxy.validate_record(corrupted) == 8

    for split_at in range(len(frame) + 1):
        framer = FrameBuffer()
        emitted = framer.feed(frame[:split_at])
        emitted += framer.feed(frame[split_at:])

        assert emitted == [frame]
        assert framer.pending == b""


def test_sanitized_captured_protocol_06_frame_survives_coalescing(
    sanitized_protocol_06_capture,
):
    captured = sanitized_protocol_06_capture
    frames = [captured, make_frame(protocol=2), captured]

    assert FrameBuffer().feed(b"".join(frames)) == frames


def test_frame_buffer_rejects_impossible_and_oversized_lengths():
    impossible = b"\x00\x01\x00\x02\x00\x01\x04"
    oversized = b"\x00\x01\x00\x06\xff\xff\x01\x04"

    with pytest.raises(FrameError, match="smaller than the record type"):
        FrameBuffer().feed(impossible)

    with pytest.raises(FrameError, match="exceeds maximum"):
        FrameBuffer(max_frame_size=1024).feed(oversized)


def test_frame_buffer_bounds_an_incomplete_tail():
    framer = FrameBuffer(max_buffer_size=9)

    with pytest.raises(FrameError, match="incomplete frame buffer"):
        framer.feed(b"\x00\x01\x00\x02\x00\x08" + b"abcd")


class MemorySocket:
    def __init__(self, recv_chunks=(), send_error=None):
        self.recv_chunks = list(recv_chunks)
        self.send_error = send_error
        self.sent = []
        self.closed = False
        self.timeout = None

    def recv(self, _size):
        return self.recv_chunks.pop(0)

    def sendall(self, data):
        if self.send_error:
            raise self.send_error
        self.sent.append(data)

    def settimeout(self, timeout):
        self.timeout = timeout

    def close(self):
        self.closed = True

    def getpeername(self):
        return ("127.0.0.1", 12345)


def make_conf(**overrides):
    values = {
        "blockcmd": False,
        "recwl": {"0104", "0150"},
        "verbose": False,
        "diagnostic_logging": False,
        "minrecl": 10_000,
        "noipf": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def make_proxy_pair(source, target, *other_sockets):
    proxy = object.__new__(grottproxy.Proxy)
    proxy.input_list = [source, target, *other_sockets]
    proxy.channel = {source: target, target: source}
    proxy.framers = {source: FrameBuffer(), target: FrameBuffer()}
    proxy.frame_started_at = {}
    return proxy


def test_validate_record_handles_short_unknown_and_bad_crc_without_raising():
    assert grottproxy.validate_record(b"") == 8
    assert grottproxy.validate_record(b"\x00" * 7) == 8
    assert grottproxy.validate_record(make_frame(protocol=9)) == 8
    assert grottproxy.validate_record(make_frame(protocol=2).hex()) == 0

    bad_crc = bytearray(make_frame(protocol=6))
    bad_crc[-1] ^= 0xFF
    assert grottproxy.validate_record(bytes(bad_crc)) == 8


def test_invalid_crc_is_forwarded_exactly_but_not_decoded(monkeypatch):
    packet = bytearray(make_frame(protocol=6))
    packet[-1] ^= 0xFF
    packet = bytes(packet)
    source = MemorySocket()
    target = MemorySocket()
    proxy = make_proxy_pair(source, target)
    proxy.s = source
    proxy.data = packet
    decoded = []
    monkeypatch.setattr(grottproxy, "procdata", lambda *_args: decoded.append(True))

    proxy.on_recv(make_conf())

    assert target.sent == [packet]
    assert decoded == []


@pytest.mark.parametrize(
    ("command", "noipf"), [("001f", False), ("0011", True)]
)
def test_corrupted_protocol_06_configure_exemptions_remain_blocked(
    monkeypatch, command, noipf
):
    packet = make_encrypted_configure_frame(command, corrupt_crc=True)
    source = MemorySocket()
    target = MemorySocket()
    proxy = make_proxy_pair(source, target)
    proxy.s = source
    proxy.data = packet
    decoded = []
    monkeypatch.setattr(grottproxy, "procdata", lambda *_args: decoded.append(True))

    proxy.on_recv(make_conf(blockcmd=True, recwl=set(), noipf=noipf))

    assert target.sent == []
    assert decoded == []
    assert source.closed is False
    assert target.closed is False


@pytest.mark.parametrize(
    ("command", "noipf"), [("001f", False), ("0011", True)]
)
def test_valid_protocol_06_configure_exemptions_still_forward(command, noipf):
    packet = make_encrypted_configure_frame(command)
    source = MemorySocket()
    target = MemorySocket()
    proxy = make_proxy_pair(source, target)
    proxy.s = source
    proxy.data = packet

    proxy.on_recv(make_conf(blockcmd=True, recwl=set(), noipf=noipf))

    assert target.sent == [packet]


def test_record_whitelist_still_allows_corrupted_configure_frame():
    packet = make_encrypted_configure_frame("001f", corrupt_crc=True)
    source = MemorySocket()
    target = MemorySocket()
    proxy = make_proxy_pair(source, target)
    proxy.s = source
    proxy.data = packet

    proxy.on_recv(make_conf(blockcmd=True, recwl={"0118"}))

    assert target.sent == [packet]


def test_telemetry_decode_failure_does_not_undo_raw_forwarding(monkeypatch):
    packet = make_frame(protocol=2)
    source = MemorySocket()
    target = MemorySocket()
    proxy = make_proxy_pair(source, target)
    proxy.s = source
    proxy.data = packet
    monkeypatch.setattr(grottproxy, "procdata", lambda *_args: (_ for _ in ()).throw(ValueError("bad layout")))

    proxy.on_recv(make_conf(minrecl=0))

    assert target.sent == [packet]


def test_influx_sink_failure_does_not_undo_raw_forwarding(monkeypatch, capsys):
    secret = "SENTINEL_INFLUX_PROXY_SECRET"
    packet = make_frame(protocol=2)
    source = MemorySocket()
    target = MemorySocket()
    proxy = make_proxy_pair(source, target)
    proxy.s = source
    proxy.data = packet
    error_type = getattr(grottdata, "InfluxSinkError", RuntimeError)
    monkeypatch.setattr(
        grottproxy,
        "procdata",
        lambda *_args: (_ for _ in ()).throw(error_type(secret)),
    )

    proxy.on_recv(make_conf(minrecl=0))

    output = capsys.readouterr().out
    assert target.sent == [packet]
    assert secret not in output
    assert "InfluxSinkError" in output


def test_telemetry_decode_error_log_does_not_expose_exception_message(
    monkeypatch, capsys
):
    secret = "SENTINEL_TELEMETRY_SECRET"
    packet = make_frame(protocol=2)
    source = MemorySocket()
    target = MemorySocket()
    proxy = make_proxy_pair(source, target)
    proxy.s = source
    proxy.data = packet
    monkeypatch.setattr(
        grottproxy,
        "procdata",
        lambda *_args: (_ for _ in ()).throw(RuntimeError(secret)),
    )

    proxy.on_recv(make_conf(minrecl=0))

    output = capsys.readouterr().out
    assert secret not in output
    assert "RuntimeError" in output


def test_short_decrypt_error_log_does_not_expose_exception_message(
    monkeypatch, capsys
):
    secret = "SENTINEL_DECRYPT_SECRET"
    packet = make_frame(protocol=6)
    source = MemorySocket()
    target = MemorySocket()
    proxy = make_proxy_pair(source, target)
    proxy.s = source
    proxy.data = packet
    monkeypatch.setattr(
        grottproxy,
        "decrypt",
        lambda *_args: (_ for _ in ()).throw(ValueError(secret)),
    )

    proxy.on_recv(make_conf(verbose=True, diagnostic_logging=True))

    output = capsys.readouterr().out
    assert secret not in output
    assert "ValueError" in output


def test_malformed_record_error_log_does_not_expose_exception_message(
    monkeypatch, capsys
):
    secret = "SENTINEL_MALFORMED_SECRET"
    packet = make_frame(protocol=2)
    source = MemorySocket([packet])
    target = MemorySocket()
    proxy = make_proxy_pair(source, target)
    monkeypatch.setattr(
        proxy,
        "on_recv",
        lambda *_args: (_ for _ in ()).throw(RuntimeError(secret)),
    )

    assert proxy._read_socket(source, make_conf()) is False

    output = capsys.readouterr().out
    assert secret not in output
    assert "RuntimeError" in output


def test_transport_error_log_does_not_expose_exception_message(capsys):
    secret = "SENTINEL_TRANSPORT_SECRET"

    class FailingReadSocket(MemorySocket):
        def recv(self, _size):
            raise OSError(secret)

    source = FailingReadSocket()
    target = MemorySocket()
    proxy = make_proxy_pair(source, target)

    assert proxy._read_socket(source, make_conf(verbose=True)) is False

    output = capsys.readouterr().out
    assert secret not in output
    assert "OSError" in output


def test_socket_reader_waits_for_complete_frame_and_forwards_it_once(monkeypatch):
    packet = make_frame(protocol=2, payload=b"fragmented")
    source = MemorySocket([packet[:5], packet[5:]])
    target = MemorySocket()
    proxy = make_proxy_pair(source, target)
    monkeypatch.setattr(grottproxy, "procdata", lambda *_args: None)

    assert proxy._read_socket(source, make_conf()) is True
    assert target.sent == []
    assert proxy._read_socket(source, make_conf()) is True
    assert target.sent == [packet]


def test_socket_reader_forwards_valid_prefix_before_coalesced_protocol_error(
    monkeypatch,
):
    valid = make_frame(protocol=2, payload=b"forward before failure")
    unsupported = make_frame(protocol=9, payload=b"terminal error")
    source = MemorySocket([valid + unsupported])
    target = MemorySocket()
    proxy = make_proxy_pair(source, target)
    monkeypatch.setattr(grottproxy, "procdata", lambda *_args: None)

    assert proxy._read_socket(source, make_conf()) is False

    assert target.sent == [valid]
    assert source.closed is True
    assert target.closed is True


def test_command_block_policy_runs_only_after_the_complete_frame():
    packet = make_frame(protocol=2, record_type=b"\x01\x41", payload=b"command")
    source = MemorySocket([packet[:7], packet[7:]])
    target = MemorySocket()
    proxy = make_proxy_pair(source, target)
    conf = make_conf(blockcmd=True, recwl=set())

    assert proxy._read_socket(source, conf) is True
    assert target.sent == []
    assert proxy._read_socket(source, conf) is True
    assert target.sent == []


def test_unknown_protocol_cleanup_does_not_touch_another_usable_pair(monkeypatch):
    valid = make_frame(protocol=2, payload=b"still usable")
    bad_source = MemorySocket([make_frame(protocol=9, payload=b"unsupported")])
    bad_target = MemorySocket()
    good_source = MemorySocket([valid])
    good_target = MemorySocket()
    proxy = make_proxy_pair(bad_source, bad_target, good_source, good_target)
    proxy.channel.update({good_source: good_target, good_target: good_source})
    proxy.framers.update({good_source: FrameBuffer(), good_target: FrameBuffer()})

    assert proxy._read_socket(bad_source, make_conf()) is False

    assert bad_source.closed is True
    assert bad_target.closed is True
    assert good_source.closed is False
    assert good_target.closed is False
    assert good_source in proxy.input_list
    assert good_target in proxy.input_list
    monkeypatch.setattr(grottproxy, "procdata", lambda *_args: None)

    assert proxy._read_socket(good_source, make_conf()) is True
    assert good_target.sent == [valid]


def test_broken_pipe_cleanup_is_connection_local():
    packet = make_frame(protocol=2)
    source = MemorySocket([packet])
    target = MemorySocket(send_error=BrokenPipeError("gone"))
    good_source = MemorySocket()
    good_target = MemorySocket()
    proxy = make_proxy_pair(source, target, good_source, good_target)
    proxy.channel.update({good_source: good_target, good_target: good_source})
    proxy.framers.update({good_source: FrameBuffer(), good_target: FrameBuffer()})

    assert proxy._read_socket(source, make_conf()) is False

    assert source.closed is True
    assert target.closed is True
    assert good_source.closed is False
    assert good_target.closed is False


def test_incomplete_frame_deadline_closes_only_the_stalled_pair():
    stalled_source = MemorySocket()
    stalled_target = MemorySocket()
    good_source = MemorySocket()
    good_target = MemorySocket()
    proxy = make_proxy_pair(
        stalled_source, stalled_target, good_source, good_target
    )
    proxy.channel.update({good_source: good_target, good_target: good_source})
    proxy.framers.update({good_source: FrameBuffer(), good_target: FrameBuffer()})
    proxy.frame_started_at[stalled_source] = 100.0

    proxy._expire_incomplete_frames(
        make_conf(), now=100.0 + grottproxy.INCOMPLETE_FRAME_TIMEOUT + 0.1
    )

    assert stalled_source.closed is True
    assert stalled_target.closed is True
    assert good_source.closed is False
    assert good_target.closed is False


def test_completed_partial_frame_gives_new_tail_a_fresh_deadline(monkeypatch):
    old_frame = make_frame(protocol=2, payload=b"old partial")
    new_frame = make_frame(protocol=2, payload=b"new partial")
    source = MemorySocket(
        [old_frame[:5], old_frame[5:] + new_frame[:4]]
    )
    target = MemorySocket()
    proxy = make_proxy_pair(source, target)
    times = iter([100.0, 129.0])
    monkeypatch.setattr(grottproxy.time, "monotonic", lambda: next(times))

    assert proxy._read_socket(source, make_conf()) is True
    assert proxy.frame_started_at[source] == 100.0
    assert proxy._read_socket(source, make_conf()) is True
    assert target.sent == [old_frame]
    assert proxy.framers[source].pending == new_frame[:4]
    assert proxy.frame_started_at[source] == 129.0

    proxy._expire_incomplete_frames(make_conf(), now=130.1)

    assert source.closed is False
    assert target.closed is False


def test_send_all_retries_partial_writes_until_every_byte_is_sent():
    class PartialSocket:
        def __init__(self):
            self.written = bytearray()

        def send(self, data):
            accepted = bytes(data[:2])
            self.written.extend(accepted)
            return len(accepted)

    sock = PartialSocket()

    grottproxy.send_all(sock, b"exact bytes")

    assert bytes(sock.written) == b"exact bytes"


class ConnectSocket(MemorySocket):
    def __init__(self):
        super().__init__()
        self.events = []

    def settimeout(self, timeout):
        super().settimeout(timeout)
        self.events.append(("timeout", timeout))

    def connect(self, address):
        self.events.append(("connect", address))


def test_upstream_connect_sets_timeout_before_connect(monkeypatch):
    sock = ConnectSocket()
    monkeypatch.setattr(grottproxy.socket, "socket", lambda *_args: sock)

    assert grottproxy.Forward().start("growatt.example", 5279) is sock
    assert sock.events[0] == ("timeout", grottproxy.SOCKET_IO_TIMEOUT)
    assert sock.events[1] == ("connect", ("growatt.example", 5279))


@pytest.mark.parametrize("failure_point", ["settimeout", "connect"])
def test_upstream_connection_error_log_does_not_expose_exception_message(
    monkeypatch, capsys, failure_point
):
    secret = f"SENTINEL_FORWARD_{failure_point.upper()}_SECRET"

    class FailingConnectSocket(ConnectSocket):
        def settimeout(self, timeout):
            if failure_point == "settimeout":
                raise OSError(secret)
            super().settimeout(timeout)

        def connect(self, address):
            if failure_point == "connect":
                raise OSError(secret)
            super().connect(address)

    sock = FailingConnectSocket()
    monkeypatch.setattr(grottproxy.socket, "socket", lambda *_args: sock)

    assert grottproxy.Forward().start("growatt.example", 5279) is False

    output = capsys.readouterr().out
    assert sock.closed is True
    assert secret not in output
    assert "OSError" in output


class AcceptSocket:
    def __init__(self, *clients):
        self.clients = list(clients)

    def accept(self):
        return self.clients.pop(0), ("127.0.0.1", 12345)


def make_accepting_proxy(client, *established_sockets):
    proxy = object.__new__(grottproxy.Proxy)
    proxy.server = AcceptSocket(client)
    proxy.forward_to = ("growatt.example", 5279)
    proxy.input_list = list(established_sockets)
    proxy.channel = {}
    proxy.framers = {}
    proxy.frame_started_at = {}
    proxy._init_connector()
    return proxy


def test_pending_upstream_connect_does_not_delay_established_forwarding(
    monkeypatch,
):
    started = threading.Event()
    release = threading.Event()
    pending_client = MemorySocket()
    pending_upstream = MemorySocket()
    packet = make_frame(protocol=2, payload=b"established pair remains live")
    established_source = MemorySocket([packet])
    established_target = MemorySocket()
    proxy = make_accepting_proxy(
        pending_client, established_source, established_target
    )
    proxy.channel.update(
        {
            established_source: established_target,
            established_target: established_source,
        }
    )
    proxy.framers.update(
        {
            established_source: FrameBuffer(),
            established_target: FrameBuffer(),
        }
    )

    class BlockingForward:
        def start(self, _host, _port):
            started.set()
            assert release.wait(2)
            return pending_upstream

    monkeypatch.setattr(grottproxy, "Forward", BlockingForward)
    monkeypatch.setattr(grottproxy, "procdata", lambda *_args: None)

    try:
        proxy.on_accept(make_conf())
        assert started.wait(1)
        assert len(proxy._pending_connections) == 1

        assert proxy._read_socket(established_source, make_conf()) is True
        assert established_target.sent == [packet]
    finally:
        release.set()
        for future in list(proxy._pending_connections):
            future.result(timeout=2)
        proxy._drain_pending_connections(make_conf())
        proxy._shutdown_connector()


def test_completed_upstream_connect_pairs_client_exactly_once(monkeypatch):
    client = MemorySocket()
    upstream = MemorySocket()
    proxy = make_accepting_proxy(client)

    class SuccessfulForward:
        def start(self, _host, _port):
            return upstream

    monkeypatch.setattr(grottproxy, "Forward", SuccessfulForward)

    try:
        proxy.on_accept(make_conf())
        future = next(iter(proxy._pending_connections))
        assert future.result(timeout=2) is upstream

        proxy._drain_pending_connections(make_conf())
        proxy._drain_pending_connections(make_conf())

        assert proxy.input_list.count(client) == 1
        assert proxy.input_list.count(upstream) == 1
        assert proxy.channel == {client: upstream, upstream: client}
        assert proxy._pending_connections == {}
    finally:
        proxy._shutdown_connector()


def test_failed_upstream_connect_closes_accepted_client(monkeypatch):
    client = MemorySocket()
    proxy = make_accepting_proxy(client)

    class FailedForward:
        def start(self, _host, _port):
            return False

    monkeypatch.setattr(grottproxy, "Forward", FailedForward)

    try:
        proxy.on_accept(make_conf())
        future = next(iter(proxy._pending_connections))
        assert future.result(timeout=2) is False

        proxy._drain_pending_connections(make_conf())

        assert client.closed is True
        assert proxy.input_list == []
        assert proxy.channel == {}
        assert proxy._pending_connections == {}
    finally:
        proxy._shutdown_connector()


def test_pending_connect_cap_closes_excess_client_without_submission():
    excess_client = MemorySocket()
    proxy = make_accepting_proxy(excess_client)
    pending = [Future() for _ in range(grottproxy.MAX_PENDING_CONNECTIONS)]
    proxy._pending_connections = {
        future: (MemorySocket(), ("127.0.0.1", index))
        for index, future in enumerate(pending)
    }

    try:
        proxy.on_accept(make_conf())

        assert excess_client.closed is True
        assert len(proxy._pending_connections) == grottproxy.MAX_PENDING_CONNECTIONS
    finally:
        proxy._shutdown_connector()


def test_connector_shutdown_closes_pending_client_and_late_upstream(
    monkeypatch,
):
    started = threading.Event()
    release = threading.Event()
    upstream_closed = threading.Event()
    client = MemorySocket()

    class ObservedSocket(MemorySocket):
        def close(self):
            super().close()
            upstream_closed.set()

    upstream = ObservedSocket()
    proxy = make_accepting_proxy(client)

    class BlockingForward:
        def start(self, _host, _port):
            started.set()
            assert release.wait(2)
            return upstream

    monkeypatch.setattr(grottproxy, "Forward", BlockingForward)

    proxy.on_accept(make_conf())
    assert started.wait(1)
    future = next(iter(proxy._pending_connections))

    proxy._shutdown_connector()
    assert client.closed is True
    assert proxy._pending_connections == {}

    release.set()
    assert future.result(timeout=2) is upstream
    assert upstream_closed.wait(1)
    assert upstream.closed is True


def test_connector_shutdown_closes_racing_upstream_exactly_once():
    client = MemorySocket()

    class CloseCountingSocket(MemorySocket):
        def __init__(self):
            super().__init__()
            self.close_count = 0

        def close(self):
            self.close_count += 1
            super().close()

    upstream = CloseCountingSocket()

    class CompletesDuringCancel(Future):
        def __init__(self):
            super().__init__()
            assert self.set_running_or_notify_cancel() is True

        def cancel(self):
            cancelled = super().cancel()
            assert cancelled is False
            self.set_result(upstream)
            return cancelled

    future = CompletesDuringCancel()

    class RacingExecutor:
        def submit(self, *_args):
            return future

        def shutdown(self, **_kwargs):
            pass

    proxy = make_accepting_proxy(client)
    proxy._connector.shutdown(wait=False, cancel_futures=True)
    proxy._connector = RacingExecutor()

    proxy.on_accept(make_conf())
    proxy._shutdown_connector()

    assert client.closed is True
    assert upstream.close_count == 1


def test_upstream_socket_creation_failure_closes_only_accepted_client(
    monkeypatch, capsys
):
    secret = "SENTINEL_UPSTREAM_SOCKET_SECRET"
    client = MemorySocket()

    class AcceptSocket:
        def accept(self):
            return client, ("127.0.0.1", 12345)

    proxy = object.__new__(grottproxy.Proxy)
    proxy.server = AcceptSocket()
    proxy.forward_to = ("growatt.example", 5279)
    proxy.input_list = []
    proxy.channel = {}
    proxy.framers = {}
    proxy.frame_started_at = {}
    monkeypatch.setattr(
        grottproxy.socket,
        "socket",
        lambda *_args: (_ for _ in ()).throw(OSError(secret)),
    )

    conf = make_conf(verbose=True)
    proxy.on_accept(conf)
    future = next(iter(proxy._pending_connections))
    with pytest.raises(OSError):
        future.result(timeout=2)
    proxy._drain_pending_connections(conf)
    proxy._shutdown_connector()

    output = capsys.readouterr().out
    assert client.closed is True
    assert proxy.input_list == []
    assert secret not in output
    assert "OSError" in output


def test_accepted_client_is_closed_when_timeout_setup_fails(capsys):
    secret = "SENTINEL_CLIENT_TIMEOUT_SECRET"

    class TimeoutFailureSocket(MemorySocket):
        def settimeout(self, _timeout):
            raise OSError(secret)

    client = TimeoutFailureSocket()

    class AcceptSocket:
        def accept(self):
            return client, ("127.0.0.1", 12345)

    proxy = object.__new__(grottproxy.Proxy)
    proxy.server = AcceptSocket()
    proxy.forward_to = ("growatt.example", 5279)
    proxy.input_list = []
    proxy.channel = {}
    proxy.framers = {}
    proxy.frame_started_at = {}

    proxy.on_accept(make_conf(verbose=True))

    output = capsys.readouterr().out
    assert client.closed is True
    assert proxy.input_list == []
    assert proxy.channel == {}
    assert proxy.framers == {}
    assert proxy.frame_started_at == {}
    assert secret not in output
    assert "OSError" in output


class ListeningSocket(MemorySocket):
    def setsockopt(self, *_args):
        pass

    def bind(self, _address):
        pass

    def listen(self, _backlog):
        pass


def test_proxy_does_not_restore_terminating_sigpipe_handler(monkeypatch):
    calls = []
    monkeypatch.setattr(
        grottproxy, "signal", lambda *args: calls.append(args), raising=False
    )
    monkeypatch.setattr(grottproxy.socket, "socket", lambda *_args: ListeningSocket())
    monkeypatch.setattr(grottproxy.socket, "gethostname", lambda: "grott-test")
    monkeypatch.setattr(grottproxy.socket, "gethostbyname", lambda _host: "127.0.0.1")
    conf = SimpleNamespace(
        grottip="127.0.0.1",
        grottport=5279,
        growattip="growatt.example",
        growattport=5279,
    )

    grottproxy.Proxy(conf)

    assert calls == []


def test_loopback_malformed_client_does_not_stop_valid_client(monkeypatch):
    upstream_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    upstream_listener.bind(("127.0.0.1", 0))
    upstream_listener.listen(2)
    upstream_listener.settimeout(2)
    upstream_connections = []
    clients = []
    proxy = None

    try:
        conf = make_conf(
            grottip="127.0.0.1",
            grottport=0,
            growattip="127.0.0.1",
            growattport=upstream_listener.getsockname()[1],
        )
        proxy = grottproxy.Proxy(conf)
        proxy_address = proxy.server.getsockname()
        monkeypatch.setattr(grottproxy, "procdata", lambda *_args: None)

        for _ in range(2):
            client = socket.create_connection(proxy_address, timeout=2)
            clients.append(client)
            proxy.on_accept(conf)
            future = next(reversed(proxy._pending_connections))
            assert future.result(timeout=2)
            proxy._drain_pending_connections(conf)
            upstream_connection, _ = upstream_listener.accept()
            upstream_connection.settimeout(2)
            upstream_connections.append(upstream_connection)

        def accepted_side(client):
            client_address = client.getsockname()
            return next(
                sock
                for sock in proxy.channel
                if sock.getpeername() == client_address
            )

        malformed_side = accepted_side(clients[0])
        valid_side = accepted_side(clients[1])
        clients[0].sendall(b"\x00\x01\x00\x02\x00\x01\x04")

        assert proxy._read_socket(malformed_side, conf) is False
        assert valid_side in proxy.input_list

        packet = make_frame(protocol=6, payload=b"loopback exact bytes")
        clients[1].sendall(packet)

        assert proxy._read_socket(valid_side, conf) is True
        received = bytearray()
        while len(received) < len(packet):
            received.extend(upstream_connections[1].recv(len(packet) - len(received)))
        assert bytes(received) == packet
    finally:
        if proxy is not None:
            for source in list(proxy.channel):
                proxy._close_socket(source, make_conf())
            proxy._shutdown_connector()
            proxy.server.close()
        for sock in clients + upstream_connections + [upstream_listener]:
            sock.close()
