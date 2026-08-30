import ast
from pathlib import Path
import socket

import pytest

import grottserver


KEEPALIVE_OPTION_NAMES = ("TCP_KEEPIDLE", "TCP_KEEPINTVL", "TCP_KEEPCNT")


@pytest.fixture
def tcp_socket():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        yield sock
    finally:
        sock.close()


def test_enable_keepalive_turns_on_so_keepalive(tcp_socket):
    assert not tcp_socket.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE)

    grottserver.enable_keepalive(tcp_socket)

    # Linux reports 1 while macOS reports the option's flag bit, so assert that
    # the option is set rather than comparing against a specific value.
    assert tcp_socket.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE)


@pytest.mark.parametrize(
    ("option_name", "expected"),
    [
        ("TCP_KEEPIDLE", grottserver.KeepaliveIdle),
        ("TCP_KEEPINTVL", grottserver.KeepaliveInterval),
        ("TCP_KEEPCNT", grottserver.KeepaliveCount),
    ],
)
def test_enable_keepalive_applies_configured_probe_timings(
    tcp_socket, option_name, expected
):
    option = getattr(socket, option_name, None)
    if option is None:
        pytest.skip(f"{option_name} is unavailable on this platform")

    grottserver.enable_keepalive(tcp_socket)

    assert tcp_socket.getsockopt(socket.IPPROTO_TCP, option) == expected


def test_enable_keepalive_skips_options_the_platform_lacks(tcp_socket, monkeypatch):
    """Platforms without the Linux TCP_* knobs still get SO_KEEPALIVE."""
    for option_name in KEEPALIVE_OPTION_NAMES:
        monkeypatch.delattr(socket, option_name, raising=False)

    grottserver.enable_keepalive(tcp_socket)

    assert tcp_socket.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE)


def test_handle_new_connection_enables_keepalive_on_accepted_socket():
    """Without this call an abandoned session is never reaped, and each one
    permanently consumes a source port from the datalogger's small pool."""
    tree = ast.parse(
        Path(grottserver.__file__).read_text(encoding="utf-8"), "grottserver.py"
    )
    handler = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "handle_new_connection"
    )
    called_names = {
        node.func.id
        for node in ast.walk(handler)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert "enable_keepalive" in called_names
