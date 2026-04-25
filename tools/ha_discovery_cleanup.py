#!/usr/bin/env python3
"""Dry-run first cleanup helper for retained Grott MQTT discovery topics."""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from typing import Iterable


DEFAULT_PREFIX = "homeassistant/sensor/grott"


@dataclass(frozen=True)
class CleanupTarget:
    topic: str
    attribute: str


def normalize_prefix(prefix: str) -> str:
    return str(prefix or DEFAULT_PREFIX).strip("/")


def discovery_pattern(prefix: str, device: str) -> str:
    return f"{normalize_prefix(prefix)}/{device}_+/config"


def extract_attribute(topic: str, device: str, prefix: str = DEFAULT_PREFIX) -> str | None:
    head = f"{normalize_prefix(prefix)}/{device}_"
    tail = "/config"
    if not topic.startswith(head) or not topic.endswith(tail):
        return None
    attribute = topic[len(head) : -len(tail)]
    if not attribute or "/" in attribute:
        return None
    return attribute


def parse_keep(value: str | None) -> set[str]:
    if not value:
        return set()
    return {item.strip() for item in value.split(",") if item.strip()}


def cleanup_plan(
    topics: Iterable[str],
    device: str,
    prefix: str = DEFAULT_PREFIX,
    keep: Iterable[str] = (),
    clear_all: bool = False,
) -> list[CleanupTarget]:
    keep_set = set(keep)
    targets: list[CleanupTarget] = []
    seen: set[str] = set()
    for topic in sorted(topics):
        if topic in seen:
            continue
        seen.add(topic)
        attribute = extract_attribute(topic, device, prefix)
        if attribute is None:
            continue
        if clear_all or attribute not in keep_set:
            targets.append(CleanupTarget(topic=topic, attribute=attribute))
    return targets


def mqtt_client(username: str | None, password: str | None):
    try:
        import paho.mqtt.client as mqtt
    except ImportError as exc:  # pragma: no cover - exercised by user environment
        raise SystemExit("Install paho-mqtt before using MQTT cleanup.") from exc

    client = mqtt.Client()
    if username:
        client.username_pw_set(username, password)
    return client


def discover_topics(args: argparse.Namespace) -> list[str]:
    topics: list[str] = []
    pattern = discovery_pattern(args.prefix, args.device)
    client = mqtt_client(args.username, args.password)

    def on_connect(client, _userdata, _flags, rc):
        if rc != 0:
            raise SystemExit(f"MQTT connect failed with rc={rc}")
        client.subscribe(pattern)

    def on_message(_client, _userdata, message):
        if message.retain and message.payload:
            topics.append(message.topic)

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(args.host, args.port, keepalive=30)
    client.loop_start()
    time.sleep(args.timeout)
    client.loop_stop()
    client.disconnect()
    return sorted(set(topics))


def clear_topics(args: argparse.Namespace, targets: list[CleanupTarget]) -> None:
    client = mqtt_client(args.username, args.password)
    client.connect(args.host, args.port, keepalive=30)
    client.loop_start()
    try:
        for target in targets:
            info = client.publish(target.topic, payload=b"", qos=0, retain=True)
            info.wait_for_publish()
    finally:
        client.loop_stop()
        client.disconnect()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Find and optionally clear retained Home Assistant MQTT discovery topics published by Grott."
    )
    parser.add_argument("--host", required=True, help="MQTT broker hostname or IP")
    parser.add_argument("--port", type=int, default=1883, help="MQTT broker port")
    parser.add_argument("--username", help="MQTT username")
    parser.add_argument("--password", help="MQTT password")
    parser.add_argument("--device", required=True, help="Grott device id prefix, for example a datalogger serial")
    parser.add_argument("--prefix", default=DEFAULT_PREFIX, help=f"Discovery prefix, default {DEFAULT_PREFIX}")
    parser.add_argument("--keep", help="Comma-separated attributes to keep; all others are planned for cleanup")
    parser.add_argument("--all", action="store_true", help="Clear every retained discovery topic for the device")
    parser.add_argument("--execute", action="store_true", help="Publish retained empty payloads; default is dry-run")
    parser.add_argument("--timeout", type=float, default=2.0, help="Seconds to wait for retained discovery topics")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    keep = parse_keep(args.keep)
    if not args.all and not keep:
        parser.error("choose --all or --keep so the cleanup intent is explicit")

    topics = discover_topics(args)
    targets = cleanup_plan(topics, args.device, args.prefix, keep=keep, clear_all=args.all)

    if not targets:
        print("No retained Grott discovery topics matched the cleanup plan.")
        return 0

    action = "Clearing" if args.execute else "Dry run, would clear"
    print(f"{action} {len(targets)} retained discovery topic(s):")
    for target in targets:
        print(f"  {target.topic}  ({target.attribute})")

    if args.execute:
        clear_topics(args, targets)
        print("Cleanup publish completed.")
    else:
        print("Re-run with --execute to publish empty retained payloads.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
