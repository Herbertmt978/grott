"""Cleanup of an accepted datalogger connection once its peer has vanished.

Both handlers under test used to ask the socket itself who the peer was, at a
point where the peer is already gone. getpeername() raises there, and neither
handler survived it: handle_writable_socket() left client_address unbound and
raised NameError on every pass, and close_connection() aborted before it could
drop the send queue or close the file descriptor.
"""

import ast
from pathlib import Path
import socket
import select
from unittest.mock import Mock

import pytest

import grottserver


def method_source(name):
    tree = ast.parse(
        Path(grottserver.__file__).read_text(encoding="utf-8"), "grottserver.py"
    )
    return next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def calls_getpeername(node):
    return any(
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "getpeername"
        for call in ast.walk(node)
    )


@pytest.fixture
def registries(monkeypatch):
    """grottserver builds these under `if __name__ == "__main__"`, so an
    imported module has neither."""
    # The server owns the supplied registry, not the module's main-only global.
    monkeypatch.delattr(grottserver, "send_queuereg", raising=False)
    monkeypatch.setattr(grottserver, "loggerreg", {}, raising=False)
    return {}


@pytest.fixture
def server(registries):
    instance = grottserver.sendrecvserver("127.0.0.1", 0, registries)
    try:
        yield instance
    finally:
        for sock in set(instance.inputs) | set(instance.outputs):
            try:
                sock.close()
            except OSError:
                pass


@pytest.fixture
def accepted(server):
    """An accepted connection, plus the client end so a test can drop it."""
    client = socket.create_connection(server.server.getsockname())
    server.handle_new_connection(server.server)

    connection = next(s for s in server.inputs if s is not server.server)
    qname = "{}_{}".format(*client.getsockname())
    try:
        yield connection, client, qname
    finally:
        client.close()


def test_accepting_a_connection_records_its_peer(server, accepted):
    connection, client, _ = accepted

    assert server.peers[connection] == client.getsockname()


def test_closing_after_the_peer_vanished_drops_the_send_queue(
    server, accepted, registries
):
    connection, client, qname = accepted
    assert qname in registries

    client.close()
    server.close_connection(connection)

    assert qname not in registries


@pytest.mark.parametrize(
    "name", ["handle_new_connection", "close_connection", "handle_writable_socket"]
)
def test_cleanup_paths_never_ask_a_dropped_socket_for_its_peer(name):
    """Neither handler may reach for getpeername(): both run at points where
    the peer can already be gone, and the call raises there. Whether it
    actually raises is the kernel's timing to decide, which is why this is
    asserted structurally rather than by racing a loopback disconnect."""
    assert not calls_getpeername(method_source(name))


def test_closing_after_the_peer_vanished_still_closes_the_socket(server, accepted):
    connection, client, _ = accepted

    client.close()
    server.close_connection(connection)

    assert connection.fileno() == -1


def test_closing_deregisters_the_connection(server, accepted):
    connection, client, _ = accepted

    client.close()
    server.close_connection(connection)

    assert connection not in server.inputs
    assert connection not in server.outputs
    assert connection not in server.peers


def test_closing_twice_is_harmless(server, accepted):
    connection, client, _ = accepted

    client.close()
    server.close_connection(connection)
    server.close_connection(connection)

    assert connection.fileno() == -1


def test_writable_handler_reports_no_error_for_an_unregistered_socket(
    server, accepted, capsys
):
    """The select loop can still offer a socket that close_connection() just
    reaped; that must not surface as a server-thread exception."""
    connection, _, _ = accepted
    server.peers.pop(connection)

    server.handle_writable_socket(connection)

    assert "exception in server thread" not in capsys.readouterr().out


def test_writable_handler_still_sends_queued_responses(server, accepted, registries):
    """The peer lookup must not have cost the handler its actual job."""
    connection, client, qname = accepted
    registries[qname].put(b"\x00\x01ack")

    server.handle_writable_socket(connection)

    client.settimeout(5)
    assert client.recv(16) == b"\x00\x01ack"


def test_accept_uses_returned_peer_when_socket_lookup_already_fails(server, registries):
    connection = Mock()
    connection.getpeername.side_effect = OSError("peer disconnected")
    listener = Mock()
    listener.accept.return_value = (connection, ("127.0.0.1", 12345))

    server.handle_new_connection(listener)

    connection.getpeername.assert_not_called()
    assert server.peers[connection] == ("127.0.0.1", 12345)
    assert "127.0.0.1_12345" in registries
    server.close_connection(connection)
    assert "127.0.0.1_12345" not in registries
    connection.close.assert_called_once()


def test_close_removes_only_matching_logger(server, accepted):
    connection, client, _ = accepted
    address, port = client.getsockname()
    grottserver.loggerreg.update({
        "closed": {"ip": address, "port": port},
        "other": {"ip": address, "port": port + 1},
    })
    server.close_connection(connection)
    assert set(grottserver.loggerreg) == {"other"}


def test_socket_is_closed_even_when_logger_registry_is_malformed(server, accepted, registries):
    connection, _, qname = accepted
    grottserver.loggerreg["malformed"] = {}
    server.close_connection(connection)
    assert connection.fileno() == -1
    assert qname not in registries
    assert connection not in server.peers


def test_readable_eof_reaps_repeated_connections(server, registries, capsys):
    """Exercise select/read/cleanup, not only direct calls to close_connection."""
    for _ in range(100):
        client = socket.create_connection(server.server.getsockname())
        try:
            server.handle_new_connection(server.server)
            connection = next(sock for sock in server.inputs if sock is not server.server)
            address, port = client.getsockname()
            grottserver.loggerreg["reconnecting"] = {"ip": address, "port": port}
            client.shutdown(socket.SHUT_WR)
            readable, _, _ = select.select([connection], [], [], 2)
            assert readable == [connection]
            server.handle_readable_socket(connection)
            assert connection.fileno() == -1
            assert server.inputs == [server.server]
            assert not server.outputs
            assert not server.peers
            assert not registries
            assert not grottserver.loggerreg
        finally:
            client.close()
    assert "exception in server thread" not in capsys.readouterr().out


def test_read_error_reaps_registered_connection(server, registries):
    connection = Mock()
    connection.recv.side_effect = ConnectionResetError("peer reset")
    listener = Mock()
    listener.accept.return_value = (connection, ("127.0.0.1", 12345))
    server.handle_new_connection(listener)

    server.handle_readable_socket(connection)

    connection.close.assert_called_once()
    assert connection not in server.inputs
    assert connection not in server.outputs
    assert connection not in server.peers
    assert not registries
