#!/usr/bin/env python3
"""Validate the Grott Home Assistant add-on repository metadata."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
ADDON_DIR = ROOT / "addons" / "grott"
CONFIG_PATH = ADDON_DIR / "config.yaml"
REPOSITORY_PATH = ROOT / "repository.yaml"
DOCKERFILE_PATH = ADDON_DIR / "Dockerfile"

EXPECTED_ARCHES = {"aarch64", "amd64", "armv7", "i386"}
EXPECTED_IMAGE = "ghcr.io/herbertmt978/grott-ha-docker"
REQUIRED_REPOSITORY_KEYS = {"name", "url", "maintainer"}
REQUIRED_ADDON_KEYS = {
    "name",
    "version",
    "slug",
    "description",
    "url",
    "arch",
    "startup",
    "boot",
    "ports",
    "options",
    "schema",
    "image",
}
REQUIRED_FILES = {
    "CHANGELOG.md",
    "DOCS.md",
    "Dockerfile",
    "README.md",
    "run.sh",
}


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return data


def require(errors: list[str], condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


def validate_repository(errors: list[str]) -> None:
    repo = load_yaml(REPOSITORY_PATH)
    missing = REQUIRED_REPOSITORY_KEYS - set(repo)
    require(errors, not missing, f"repository.yaml missing keys: {sorted(missing)}")
    require(
        errors,
        str(repo.get("url", "")).startswith("https://github.com/Herbertmt978/grott"),
        "repository.yaml url should point at the public fork",
    )


def validate_addon_config(errors: list[str]) -> None:
    addon = load_yaml(CONFIG_PATH)
    missing = REQUIRED_ADDON_KEYS - set(addon)
    require(errors, not missing, f"addons/grott/config.yaml missing keys: {sorted(missing)}")

    version = str(addon.get("version", ""))
    require(
        errors,
        bool(re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", version)),
        "add-on version must be semver-ish because it is also the GHCR tag",
    )
    require(
        errors,
        addon.get("image") == EXPECTED_IMAGE,
        f"add-on image should be {EXPECTED_IMAGE}",
    )

    slug = str(addon.get("slug", ""))
    require(
        errors,
        bool(re.fullmatch(r"[a-z0-9][a-z0-9_-]*", slug)),
        "add-on slug must be URI friendly",
    )

    arches = set(addon.get("arch") or [])
    require(errors, arches == EXPECTED_ARCHES, f"arch list should be {sorted(EXPECTED_ARCHES)}")

    options = addon.get("options") or {}
    schema = addon.get("schema") or {}
    require(errors, isinstance(options, dict), "options must be a mapping")
    require(errors, isinstance(schema, dict), "schema must be a mapping")
    if isinstance(options, dict) and isinstance(schema, dict):
        missing_schema = set(options) - set(schema)
        require(errors, not missing_schema, f"options missing schema entries: {sorted(missing_schema)}")

    require(errors, addon.get("ports", {}).get("5279/tcp") == 5279, "port 5279/tcp must be exposed")
    require(errors, "mqtt:need" in (addon.get("services") or []), "MQTT service should be declared as needed")


def validate_files(errors: list[str]) -> None:
    present = {path.name for path in ADDON_DIR.iterdir() if path.is_file()}
    missing = REQUIRED_FILES - present
    require(errors, not missing, f"add-on folder missing files: {sorted(missing)}")

    dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")
    for label in ("io.hass.version", "io.hass.type", "io.hass.arch"):
        require(errors, label in dockerfile, f"Dockerfile missing {label} label")

    version = load_yaml(CONFIG_PATH).get("version")
    require(
        errors,
        f"GROTT_REF=v{version}" in dockerfile,
        "Dockerfile default GROTT_REF should track the add-on version tag",
    )


def main() -> int:
    errors: list[str] = []
    try:
        validate_repository(errors)
        validate_addon_config(errors)
        validate_files(errors)
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        errors.append(str(exc))

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print("Home Assistant add-on repository metadata looks valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
