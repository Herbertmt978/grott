#!/usr/bin/env python3
"""Passive container health check for Grott's listening proxy port."""

from __future__ import annotations

import configparser
import os
from pathlib import Path
import sys
from typing import Iterable, Mapping


DEFAULT_PORT = 5279
DEFAULT_CONFIG_PATH = Path("/app/grott.ini")
PROC_TABLES = (Path("/proc/net/tcp"), Path("/proc/net/tcp6"))


def parse_listening_ports(lines: Iterable[str]) -> set[int]:
    """Return TCP ports whose procfs rows are in LISTEN state."""
    ports: set[int] = set()
    for line in lines:
        fields = line.split()
        if len(fields) < 4 or fields[3] != "0A":
            continue
        try:
            _address, encoded_port = fields[1].rsplit(":", 1)
            port = int(encoded_port, 16)
        except (ValueError, IndexError):
            continue
        if 1 <= port <= 65535:
            ports.add(port)
    return ports


def _ini_port(config_path: Path) -> str | None:
    """Read the proxy port from Grott's mounted INI without executing it."""
    if not config_path.exists():
        return None
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with config_path.open(encoding="utf-8") as handle:
            parser.read_file(handle)
    except (OSError, configparser.Error) as exc:
        raise ValueError("unable to read health-check configuration") from exc
    if not parser.has_option("Generic", "port"):
        return None
    return parser.get("Generic", "port", raw=True)


def resolve_port(
    environment: Mapping[str, str], config_path: Path = DEFAULT_CONFIG_PATH
) -> int:
    """Resolve the effective proxy port using env-over-INI precedence."""
    if "GROTT_HEALTH_PORT" in environment:
        raw_value = environment["GROTT_HEALTH_PORT"]
    elif "ggrottport" in environment:
        raw_value = environment["ggrottport"]
    else:
        configured_port = _ini_port(config_path)
        raw_value = str(DEFAULT_PORT) if configured_port is None else configured_port
    try:
        port = int(raw_value, 10)
    except (TypeError, ValueError) as exc:
        raise ValueError("health-check port must be an integer") from exc
    if not 1 <= port <= 65535:
        raise ValueError("health-check port must be between 1 and 65535")
    return port


def is_listening(port: int, paths: Iterable[Path] = PROC_TABLES) -> bool:
    """Read procfs TCP tables and report whether *port* is listening."""
    for path in paths:
        try:
            with path.open(encoding="ascii") as handle:
                if port in parse_listening_ports(handle):
                    return True
        except FileNotFoundError:
            continue
    return False


def main(
    environment: Mapping[str, str] = os.environ,
    paths: Iterable[Path] = PROC_TABLES,
) -> int:
    try:
        port = resolve_port(environment)
    except ValueError as exc:
        print(f"invalid health-check port: {exc}", file=sys.stderr)
        return 2
    return 0 if is_listening(port, paths) else 1


if __name__ == "__main__":
    raise SystemExit(main())
