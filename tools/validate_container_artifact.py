#!/usr/bin/env python3
"""Validate the installed Grott runtime surface inside a final container image."""

from __future__ import annotations

import hashlib
from importlib import import_module, metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from typing import Any, Iterable

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


APP_DIR = Path("/app")
VENV_SITE_PACKAGES = Path("/opt/venv/lib/python3.11/site-packages")
EXPECTED_WHEEL_VERSION = "0.47.0"
APPROVED_EXTERNAL_LAYOUTS = {
    "T06NNNNXMOD.json": {"T06NNNNXMOD"},
    "t060103xmax3.json": {"T060103XMAX"},
    "T06221b.json": {"T06221b"},
}
APPROVED_EXTERNAL_LAYOUT_SHA256 = {
    "T06NNNNXMOD.json": (
        "278a9eec3c8f008eee83daba5d3974044e276a894521314afa160b3d07372378"
    ),
    "t060103xmax3.json": (
        "9d96ecda61e5cbdafc4c6937af8909dbca224ad31ceb0cf62ea1d443d37ea10e"
    ),
    "T06221b.json": (
        "8f69cc4a294b1917a6606c8157aaec27d044a7783a7ee0dcc2970d47a391a569"
    ),
}
EXPECTED_GENERIC_LAYOUT = {
    "datalogserial": {"value": 16, "length": 10, "type": "text", "incl": "yes"},
    "pvserial": {"value": 76, "length": 10, "type": "text"},
    "pvstatus": {"value": 158, "length": 2, "type": "num"},
    "pvpowerin": {"value": 162, "length": 4, "type": "num", "divide": 10},
    "pv1voltage": {"value": 170, "length": 2, "type": "num", "divide": 10},
    "pv1current": {"value": 174, "length": 2, "type": "num", "divide": 10},
    "pv1watt": {"value": 178, "length": 4, "type": "num", "divide": 10},
    "pv2voltage": {"value": 186, "length": 2, "type": "num", "divide": 10},
    "pv2current": {"value": 190, "length": 2, "type": "num", "divide": 10},
    "pv2watt": {"value": 194, "length": 4, "type": "num", "divide": 10},
    "pvpowerout": {"value": 250, "length": 4, "type": "numx", "divide": 10},
    "pvfrequentie": {
        "value": 258,
        "length": 2,
        "type": "num",
        "divide": 100,
    },
    "pvgridvoltage": {"value": 262, "length": 2, "type": "num", "divide": 10},
    "pvgridcurrent": {"value": 266, "length": 2, "type": "num", "divide": 10},
    "pvgridpower": {"value": 270, "length": 4, "type": "num", "divide": 10},
    "pvgridvoltage2": {"value": 278, "length": 2, "type": "num", "divide": 10},
    "pvgridcurrent2": {"value": 282, "length": 2, "type": "num", "divide": 10},
    "pvgridpower2": {"value": 286, "length": 4, "type": "num", "divide": 10},
    "pvgridvoltage3": {"value": 294, "length": 2, "type": "num", "divide": 10},
    "pvgridcurrent3": {"value": 298, "length": 2, "type": "num", "divide": 10},
    "pvgridpower3": {"value": 302, "length": 4, "type": "num", "divide": 10},
    "totworktime": {"value": 346, "length": 4, "type": "num", "divide": 7200},
    "pvenergytoday": {"value": 354, "length": 4, "type": "num", "divide": 10},
    "pvenergytotal": {"value": 362, "length": 4, "type": "num", "divide": 10},
    "epvtotal": {"value": 370, "length": 4, "type": "num", "divide": 10},
    "epv1today": {"value": 378, "length": 4, "type": "num", "divide": 10},
    "epv1total": {"value": 386, "length": 4, "type": "num", "divide": 10},
    "epv2today": {"value": 394, "length": 4, "type": "num", "divide": 10},
    "epv2total": {"value": 402, "length": 4, "type": "num", "divide": 10},
    "pvtemperature": {"value": 530, "length": 2, "type": "num", "divide": 10},
    "pvipmtemperature": {
        "value": 546,
        "length": 2,
        "type": "num",
        "divide": 10,
    },
}


class ArtifactValidationError(RuntimeError):
    """Raised when a final image violates the reviewed artifact contract."""


def require(condition: bool, message: str) -> None:
    """Enforce an artifact invariant even when Python optimization is enabled."""
    if not condition:
        raise ArtifactValidationError(message)

REQUIRED_MODULES = (
    "grottconf",
    "grottdata",
    "grottlayout",
    "grottprotocol",
    "grottproxy",
    "grottserver",
    "grottsniffer",
    "grottext",
    "grottext.ha",
    "grott_ha",
)

REQUIRED_PAYLOADS = tuple(
    APP_DIR / relative
    for relative in (
        "grott.py",
        "grottconf.py",
        "grottdata.py",
        "grottlayout.py",
        "grottprotocol.py",
        "grottproxy.py",
        "grottserver.py",
        "grottsniffer.py",
        "grottext/__init__.py",
        "grottext/ha.py",
        "grott.ini",
        "grott_ha.py",
        "T06NNNNXMOD.json",
        "t060103xmax3.json",
        "T06221b.json",
    )
) + (
    Path("/usr/local/bin/container_healthcheck.py"),
    Path("/usr/local/bin/validate_container_artifact.py"),
)

FORBIDDEN_PATHS = tuple(
    Path(path)
    for path in (
        "/opt/venv/bin/pip",
        "/opt/venv/bin/pip3",
        "/opt/venv/bin/pip3.11",
        "/opt/venv/bin/wheel",
        "/opt/venv/lib/python3.11/site-packages/pip",
        "/opt/venv/lib/python3.11/site-packages/setuptools",
        "/opt/venv/lib/python3.11/site-packages/_distutils_hack",
        "/opt/venv/lib/python3.11/site-packages/distutils-precedence.pth",
        "/usr/local/bin/pip",
        "/usr/local/bin/pip3",
        "/usr/local/bin/pip3.11",
        "/usr/local/bin/wheel",
        "/usr/local/lib/python3.11/ensurepip",
        "/usr/local/lib/python3.11/site-packages/pip",
        "/usr/local/lib/python3.11/site-packages/setuptools",
        "/usr/local/lib/python3.11/site-packages/wheel",
        "/usr/local/lib/python3.11/site-packages/_distutils_hack",
        "/usr/local/lib/python3.11/site-packages/distutils-precedence.pth",
        "/app/t06NNNNX.json",
    )
)

FORBIDDEN_GLOBS = (
    "/opt/venv/lib/python3.11/site-packages/pip-*.dist-info",
    "/opt/venv/lib/python3.11/site-packages/setuptools-*.dist-info",
    "/usr/local/lib/python3.11/site-packages/pip-*.dist-info",
    "/usr/local/lib/python3.11/site-packages/setuptools-*.dist-info",
    "/usr/local/lib/python3.11/site-packages/wheel-*.dist-info",
)

FORBIDDEN_EXECUTABLES = frozenset(("gcc", "g++", "cc", "c++", "make", "ld", "as"))
FORBIDDEN_APK_PACKAGES = (
    "build-base",
    "gcc",
    "g++",
    "make",
    "binutils",
    "musl-dev",
)


def external_layout_payloads() -> list[Path]:
    """Return the external layout files Grott will auto-load from /app."""
    return sorted(
        path
        for path in APP_DIR.iterdir()
        if path.is_file()
        and ".json" in path.name
        and path.name[:1] in {"T", "t"}
    )


def read_external_layout(path: Path) -> dict[str, Any]:
    """Read one loader-visible external layout mapping."""
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    require(isinstance(payload, dict), f"external layout must be a mapping: {path.name}")
    return payload


def canonical_layout_sha256(payload: dict[str, Any]) -> str:
    """Hash parsed JSON so checkout line endings and formatting do not matter."""
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def external_layout_conflicts(
    builtin_recorddict: dict[str, Any],
    layout_payloads: Iterable[Path],
) -> dict[str, list[str]]:
    """Return built-in overrides and duplicate external record identifiers."""
    owners: dict[str, str] = {}
    conflicts: dict[str, set[str]] = {}
    for path in sorted(layout_payloads, key=lambda item: item.name):
        for layout_key in read_external_layout(path):
            if layout_key in builtin_recorddict:
                conflicts.setdefault(layout_key, set()).add(path.name)
            previous_owner = owners.get(layout_key)
            if previous_owner is not None:
                conflicts.setdefault(layout_key, set()).update(
                    (previous_owner, path.name)
                )
            else:
                owners[layout_key] = path.name
    return {
        layout_key: sorted(filenames)
        for layout_key, filenames in sorted(conflicts.items())
    }


def recorddict_for_directory(grottconf: Any, directory: Path) -> dict[str, Any]:
    """Load Grott's record dictionary using its production directory rules."""
    conf = grottconf.Conf.__new__(grottconf.Conf)
    conf.verbose = False
    previous_cwd = Path.cwd()
    try:
        os.chdir(directory)
        conf.set_reclayouts()
    finally:
        os.chdir(previous_cwd)
    return conf.recorddict


def assert_external_layout_contract(
    builtin_recorddict: dict[str, Any],
    loaded_recorddict: dict[str, Any],
    layout_payloads: Iterable[Path],
) -> None:
    """Require the exact reviewed external layout overlay in final images."""
    payloads = list(layout_payloads)
    actual_names = {path.name for path in payloads}
    expected_names = set(APPROVED_EXTERNAL_LAYOUTS)
    require(
        actual_names == expected_names,
        "external layout payload set does not match the reviewed allowlist: "
        f"expected {sorted(expected_names)}, found {sorted(actual_names)}",
    )

    definitions: dict[str, dict[str, Any]] = {}
    for path in payloads:
        payload = read_external_layout(path)
        actual_keys = set(payload)
        expected_keys = APPROVED_EXTERNAL_LAYOUTS[path.name]
        require(
            actual_keys == expected_keys,
            f"external layout keys for {path.name} must be "
            f"{sorted(expected_keys)}, found {sorted(actual_keys)}",
        )
        require(
            canonical_layout_sha256(payload)
            == APPROVED_EXTERNAL_LAYOUT_SHA256[path.name],
            f"external layout {path.name} does not match its reviewed semantic digest",
        )
        definitions[path.name] = payload

    conflicts = external_layout_conflicts(builtin_recorddict, payloads)
    require(
        not conflicts,
        f"external layout keys conflict with built-ins or each other: {conflicts}",
    )

    expected_recorddict = dict(builtin_recorddict)
    for path in payloads:
        expected_recorddict.update(definitions[path.name])
    require(
        loaded_recorddict == expected_recorddict,
        "production layout loading changed a built-in or approved external layout",
    )


def assert_generic_layout_contract(recorddict: dict[str, Any]) -> None:
    layout = recorddict.get("T06NNNNX")
    require(isinstance(layout, dict), "generic T06NNNNX layout is missing")
    actual = {
        key: {
            field: entry[field]
            for field in ("value", "length", "type", "divide", "incl")
            if field in entry
        }
        for key, entry in layout.items()
        if isinstance(entry, dict)
        and "length" in entry
        and entry.get("incl") != "no"
    }
    require(
        actual == EXPECTED_GENERIC_LAYOUT,
        "generic T06NNNNX layout does not match the verified 31-field contract",
    )


def dependency_closure_errors(distributions: Iterable[Any]) -> list[str]:
    """Return unsatisfied active distribution requirements."""
    installed_distributions = list(distributions)
    installed = {
        canonicalize_name(distribution.metadata["Name"]): distribution.version
        for distribution in installed_distributions
        if distribution.metadata.get("Name")
    }
    errors: list[str] = []
    for distribution in installed_distributions:
        owner = distribution.metadata.get("Name")
        if not owner:
            continue
        for raw_requirement in distribution.requires or ():
            requirement = Requirement(raw_requirement)
            if requirement.marker is not None and not requirement.marker.evaluate(
                {"extra": ""}
            ):
                continue
            dependency = canonicalize_name(requirement.name)
            if dependency not in installed:
                errors.append(
                    f"{owner}=={distribution.version} requires {requirement}, "
                    f"but {requirement.name} is not installed"
                )
                continue
            installed_version = installed[dependency]
            if requirement.specifier and not requirement.specifier.contains(
                installed_version, prereleases=True
            ):
                errors.append(
                    f"{owner}=={distribution.version} requires {requirement}, "
                    f"but {requirement.name}=={installed_version} is installed"
                )
    return errors


def assert_wheel_contract() -> None:
    """Keep the wheel library required by libscrc while excluding its CLI."""
    actual_version = metadata.version("wheel")
    require(
        actual_version == EXPECTED_WHEEL_VERSION,
        f"wheel library must be {EXPECTED_WHEEL_VERSION}, found {actual_version}"
    )
    libscrc = metadata.distribution("libscrc")
    requirements = [Requirement(value) for value in libscrc.requires or ()]
    require(
        any(canonicalize_name(item.name) == "wheel" for item in requirements),
        "libscrc must declare wheel as an installed runtime requirement",
    )
    require(not Path("/opt/venv/bin/wheel").exists(), "wheel CLI must be absent")


def assert_no_build_tooling() -> None:
    """Ensure final images contain no package installers or compiler toolchain."""
    present_paths = [str(path) for path in FORBIDDEN_PATHS if path.exists()]
    for pattern in FORBIDDEN_GLOBS:
        path = Path(pattern)
        present_paths.extend(str(match) for match in path.parent.glob(path.name))
    require(
        not present_paths,
        f"forbidden packaging paths remain: {present_paths}",
    )

    present_executables = sorted(
        executable for executable in FORBIDDEN_EXECUTABLES if shutil.which(executable)
    )
    require(
        not present_executables,
        f"compiler/build executables remain: {present_executables}",
    )

    for package in FORBIDDEN_APK_PACKAGES:
        result = subprocess.run(
            ("apk", "info", "--exists", package),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        require(
            result.returncode != 0,
            f"build-only APK package remains: {package}",
        )


def main() -> int:
    sys.path.insert(0, str(APP_DIR))
    for module in REQUIRED_MODULES:
        import_module(module)

    missing_payloads = [str(path) for path in REQUIRED_PAYLOADS if not path.is_file()]
    require(
        not missing_payloads,
        f"required runtime payload is missing: {missing_payloads}",
    )
    layout_payloads = external_layout_payloads()
    grottconf = import_module("grottconf")
    with TemporaryDirectory() as temporary_directory:
        builtin_recorddict = recorddict_for_directory(
            grottconf,
            Path(temporary_directory),
        )
    loaded_recorddict = recorddict_for_directory(grottconf, APP_DIR)
    assert_external_layout_contract(
        builtin_recorddict,
        loaded_recorddict,
        layout_payloads,
    )
    assert_generic_layout_contract(loaded_recorddict)

    assert_wheel_contract()
    distributions = list(metadata.distributions(path=[str(VENV_SITE_PACKAGES)]))
    closure_errors = dependency_closure_errors(distributions)
    require(
        not closure_errors,
        "runtime dependency closure is incomplete: " + "; ".join(closure_errors),
    )
    assert_no_build_tooling()
    print("Container artifact validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
