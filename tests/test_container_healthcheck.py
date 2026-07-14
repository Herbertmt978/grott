import ast
from pathlib import Path

import pytest

from tools import container_healthcheck


ROOT = Path(__file__).resolve().parents[1]


def test_passive_container_healthcheck_exists():
    assert (ROOT / "tools" / "container_healthcheck.py").is_file()


def test_parse_listening_ports_accepts_ipv4_and_ipv6_proc_rows():
    proc_rows = [
        "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode",
        "   0: 00000000:149F 00000000:0000 0A 00000000:00000000 00:00000000 00000000 10001 0 1 1 0000000000000000 100 0 0 10 0",
        "   1: 00000000000000000000000000000000:1883 00000000000000000000000000000000:0000 01 00000000:00000000 00:00000000 00000000 10001 0 2 1 0000000000000000 100 0 0 10 0",
        "malformed row",
    ]

    assert container_healthcheck.parse_listening_ports(proc_rows) == {5279}


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        ({}, 5279),
        ({"ggrottport": "6000"}, 6000),
        ({"GROTT_HEALTH_PORT": "7000", "ggrottport": "6000"}, 7000),
    ],
)
def test_resolve_port_uses_explicit_health_port_then_grott_port_then_default(
    environment, expected
):
    assert container_healthcheck.resolve_port(environment) == expected


def test_resolve_port_reads_mounted_ini_after_environment_overrides(tmp_path):
    config_path = tmp_path / "grott.ini"
    config_path.write_text("[Generic]\nport = 6001\n", encoding="utf-8")

    assert container_healthcheck.resolve_port({}, config_path) == 6001
    assert container_healthcheck.resolve_port({"ggrottport": "6002"}, config_path) == 6002
    assert (
        container_healthcheck.resolve_port(
            {"GROTT_HEALTH_PORT": "6003", "ggrottport": "6002"}, config_path
        )
        == 6003
    )


@pytest.mark.parametrize("value", ["", "0", "65536", "not-a-port", "52.79"])
def test_resolve_port_rejects_invalid_ini_port(tmp_path, value):
    config_path = tmp_path / "grott.ini"
    config_path.write_text(f"[Generic]\nport = {value}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="port"):
        container_healthcheck.resolve_port({}, config_path)


@pytest.mark.parametrize("value", ["", "0", "65536", "not-a-port", "52.79"])
def test_resolve_port_rejects_invalid_values(value):
    with pytest.raises(ValueError, match="port"):
        container_healthcheck.resolve_port({"GROTT_HEALTH_PORT": value})


def test_is_listening_reads_proc_tables_without_opening_a_network_connection(tmp_path):
    tcp = tmp_path / "tcp"
    tcp6 = tmp_path / "tcp6"
    tcp.write_text("header\n0: 00000000:149F 00000000:0000 0A remainder\n", encoding="ascii")
    tcp6.write_text("header\n", encoding="ascii")

    assert container_healthcheck.is_listening(5279, (tcp, tcp6)) is True
    assert container_healthcheck.is_listening(5280, (tcp, tcp6)) is False


def test_is_listening_ignores_missing_proc_table(tmp_path):
    assert container_healthcheck.is_listening(5279, (tmp_path / "missing",)) is False


def test_main_returns_healthy_unhealthy_and_invalid_configuration_status(tmp_path, capsys):
    tcp = tmp_path / "tcp"
    tcp.write_text("header\n0: 00000000:149F 00000000:0000 0A remainder\n", encoding="ascii")

    assert container_healthcheck.main({}, (tcp,)) == 0
    assert container_healthcheck.main({"GROTT_HEALTH_PORT": "5280"}, (tcp,)) == 1
    assert container_healthcheck.main({"GROTT_HEALTH_PORT": "invalid"}, (tcp,)) == 2
    assert "invalid health-check port" in capsys.readouterr().err


def test_healthcheck_has_no_network_library_or_connect_call():
    source = (ROOT / "tools" / "container_healthcheck.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    assert "socket" not in imports
    assert ".connect(" not in source
