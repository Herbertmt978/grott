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
RUN_PATH = ADDON_DIR / "run.sh"
LEGACY_RPI_DOCKERFILE_PATH = ROOT / "docker" / "dockerrpi"

EXPECTED_ARCHES = {"aarch64", "amd64", "armv7", "i386"}
EXPECTED_IMAGE = "ghcr.io/herbertmt978/grott-ha-docker"
PINNED_PYTHON_IMAGE = (
    "python:3.11.15-alpine3.24@"
    "sha256:25976e9d34a0fab1f278cae931f34c8303d97bf0c0d7f85b6b4dcf641d7702a4"
)
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
        addon.get("stage") == "experimental",
        "add-on stage must remain experimental until every release gate is satisfied",
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
        require(
            errors,
            options.get("mode") == "proxy" and schema.get("mode") == "list(proxy)",
            "Home Assistant add-on must expose proxy mode only",
        )

    runtime_bytes = RUN_PATH.read_bytes()
    require(
        errors,
        b"\r" not in runtime_bytes and runtime_bytes.endswith(b"\n"),
        "run.sh must use LF-only line endings",
    )
    runtime = runtime_bytes.decode("utf-8")
    ha_start = re.search(
        r'^\s*if \[ "\$\(json_get ha_plugin true\)" = "True" \]; then\s*$',
        runtime,
        re.MULTILINE,
    )
    ha_block = ""
    if ha_start:
        remainder = runtime[ha_start.end():]
        ha_end = re.search(r"^\s*fi\s*$", remainder, re.MULTILINE)
        if ha_end:
            ha_block = remainder[:ha_end.start()]
    require(
        errors,
        bool(re.search(r"^[ \t]*export[ \t]+gnomqtt=True[ \t]*$", ha_block, re.MULTILINE)),
        "run.sh ha_plugin block must disable native MQTT with gnomqtt=True",
    )
    require(
        errors,
        bool(
            re.search(
                r"^[ \t]*print\(json\.dumps\(payload(?:,.*)?\)\)[ \t]*$",
                ha_block,
                re.MULTILINE,
            )
        )
        and not re.search(
            r"^[ \t]*print\(repr\(payload\)\)[ \t]*$", ha_block, re.MULTILINE
        ),
        "run.sh must serialize gextvar with json.dumps, not repr",
    )

    require(errors, addon.get("ports", {}).get("5279/tcp") == 5279, "port 5279/tcp must be exposed")
    require(errors, "mqtt:need" in (addon.get("services") or []), "MQTT service should be declared as needed")
    require(errors, addon.get("tmpfs") is True, "add-on must enable tmpfs for writable temporary files")
    mappings = addon.get("map") or []
    require(
        errors,
        not any(str(mapping).startswith("share:") for mapping in mappings),
        "add-on must not request the unused share mapping",
    )


def validate_files(errors: list[str]) -> None:
    present = {path.name for path in ADDON_DIR.iterdir() if path.is_file()}
    missing = REQUIRED_FILES - present
    require(errors, not missing, f"add-on folder missing files: {sorted(missing)}")

    dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")
    for label in ("io.hass.version", "io.hass.type", "io.hass.arch"):
        require(errors, label in dockerfile, f"Dockerfile missing {label} label")

    require(
        errors,
        f"ARG PYTHON_IMAGE={PINNED_PYTHON_IMAGE}" in dockerfile
        and dockerfile.count("FROM ${PYTHON_IMAGE}") == 2
        and "BUILD_FROM" not in dockerfile,
        "Dockerfile must use the approved digest-pinned Python base in both stages",
    )
    require(
        errors,
        "git clone" not in dockerfile
        and "GROTT_REPO" not in dockerfile
        and "GROTT_REF" not in dockerfile
        and "COPY grott.py " in dockerfile
        and "COPY requirements.lock " in dockerfile,
        "Dockerfile must build from the reviewed repository root context without a remote clone",
    )
    require(
        errors,
        'COPY ["examples/Record Layout/", "/app/"]' in dockerfile,
        "Dockerfile must include bundled external layout JSON files",
    )
    require(
        errors,
        'exec su-exec grott:grott "$GROTT_RUNNER" -u /app/grott.py -v'
        in RUN_PATH.read_text(encoding="utf-8"),
        "run.sh must drop from Supervisor root to grott:grott before starting Grott",
    )
    require(
        errors,
        not LEGACY_RPI_DOCKERFILE_PATH.exists(),
        "legacy armv6 docker/dockerrpi must remain retired",
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
