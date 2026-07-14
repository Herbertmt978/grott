#!/usr/bin/env python3
"""Validate the installed Grott runtime surface inside a final container image."""

from __future__ import annotations

from importlib import import_module, metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Iterable

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


APP_DIR = Path("/app")
VENV_SITE_PACKAGES = Path("/opt/venv/lib/python3.11/site-packages")
EXPECTED_WHEEL_VERSION = "0.47.0"


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
    for layout_name in ("T06NNNNXMOD.json", "t060103xmax3.json", "T06221b.json"):
        with (APP_DIR / layout_name).open(encoding="utf-8") as handle:
            json.load(handle)

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
