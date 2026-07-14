from __future__ import annotations

import fnmatch
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def dockerignore_matches(path: str, pattern: str) -> bool:
    """Match the small, explicit Docker-ignore policy used by this repository."""
    normalized = path.lstrip("/")
    anchored = pattern.startswith("/")
    normalized_pattern = pattern.lstrip("/")
    if anchored:
        return fnmatch.fnmatchcase(normalized, normalized_pattern)
    if normalized_pattern.startswith("**/"):
        suffix = normalized_pattern[3:]
        candidate = PurePosixPath(normalized)
        return fnmatch.fnmatchcase(candidate.name, suffix) or candidate.match(
            normalized_pattern
        )
    if "/" not in normalized_pattern:
        candidate = PurePosixPath(normalized)
        return len(candidate.parts) == 1 and fnmatch.fnmatchcase(
            candidate.name, normalized_pattern
        )
    return PurePosixPath(normalized).match(normalized_pattern)


def docker_context_ignores(path: str) -> bool:
    ignored = False
    for raw_line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        pattern = line[1:] if negated else line
        if dockerignore_matches(path, pattern):
            ignored = not negated
    return ignored


def test_docker_context_excludes_local_secrets_captures_and_logs():
    for path in (
        "grott.ini",
        "docker/grott.ini",
        ".env",
        ".env.local",
        "docker/.env",
        "config/.env.local",
        "capture.pcap",
        "diagnostics/session.pcapng",
        "logs/grott.log",
        "private/server.key",
        "private/id_rsa",
        "credentials-prod.json",
        "config/secrets.yaml",
    ):
        assert docker_context_ignores(path), f"Docker context would include {path}"


def test_docker_engine_excludes_nested_secret_families(tmp_path):
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker CLI is unavailable")

    context = tmp_path / "context"
    context.mkdir()
    shutil.copyfile(ROOT / ".dockerignore", context / ".dockerignore")
    (context / "Dockerfile").write_text(
        "FROM scratch\nCOPY . /context/\n",
        encoding="utf-8",
    )
    (context / "safe.txt").write_text("required build input\n", encoding="utf-8")
    example_config = context / "examples" / "grott.ini"
    example_config.parent.mkdir(parents=True, exist_ok=True)
    example_config.write_text("tracked example\n", encoding="utf-8")
    secret_paths = (
        "docker/grott.ini",
        "docker/.env",
        "config/.env.local",
        "logs/grott.log",
        "private/server.key",
        "private/id_rsa",
        "diagnostics/session.pcapng",
        "config/credentials-prod.json",
        "config/secrets.yaml",
    )
    for relative_path in secret_paths:
        path = context / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic sentinel\n", encoding="utf-8")

    output = tmp_path / "output"
    result = subprocess.run(
        (
            docker,
            "buildx",
            "build",
            "--progress=plain",
            "--output",
            f"type=local,dest={output}",
            str(context),
        ),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (output / "context" / "safe.txt").is_file()
    assert (output / "context" / "examples" / "grott.ini").is_file()
    for relative_path in secret_paths:
        assert not (output / "context" / relative_path).exists(), (
            f"Docker transmitted ignored path {relative_path}"
        )


def test_docker_context_keeps_every_required_build_input():
    for path in (
        "grott.py",
        "grottconf.py",
        "grottdata.py",
        "grottlayout.py",
        "grottprotocol.py",
        "grottproxy.py",
        "grottserver.py",
        "grottsniffer.py",
        "requirements.lock",
        "examples/grott.ini",
        "examples/Home Assistent/grott_ha.py",
        "examples/Record Layout/t060103xmax3.json",
        "examples/Record Layout/T06221b.json",
        "examples/Record Layout/T06NNNNXMOD.json",
        "tools/container_healthcheck.py",
        "tools/validate_container_artifact.py",
    ):
        assert not docker_context_ignores(path), f"required build input ignored: {path}"


def test_bundled_external_layouts_are_valid_json():
    errors = []
    for path in sorted((ROOT / "examples" / "Record Layout").glob("*.json")):
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"{path.name}: {exc}")

    assert not errors


def test_gitignore_guards_local_release_secrets_and_captures():
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for required_pattern in (
        "**/grott.ini",
        "!/examples/grott.ini",
        "**/.env",
        "**/.env.*",
        "*.pcap",
        "*.pcapng",
        "*.log",
        "*.key",
        "/docs/aegis/",
    ):
        assert required_pattern in gitignore


def test_private_aegis_journal_is_ignored_even_when_not_present(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    shutil.copyfile(ROOT / ".gitignore", repository / ".gitignore")
    initialized = subprocess.run(
        ("git", "init", "--quiet"),
        cwd=repository,
        text=True,
        capture_output=True,
        check=False,
    )
    assert initialized.returncode == 0, initialized.stderr

    result = subprocess.run(
        (
            "git",
            "check-ignore",
            "--no-index",
            "--quiet",
            "docs/aegis/private-release-evidence.md",
        ),
        cwd=repository,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
