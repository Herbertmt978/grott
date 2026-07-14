from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys

import pytest

from tools import validate_container_artifact


@dataclass
class FakeDistribution:
    name: str
    version: str
    requires: list[str] | None = None

    @property
    def metadata(self) -> dict[str, str]:
        return {"Name": self.name}


def test_dependency_closure_reports_installed_non_marker_requirement_gaps():
    distributions = [
        FakeDistribution(
            "consumer",
            "1.0",
            ["present>=1", "missing>=1", "ignored; python_version < '3.0'"],
        ),
        FakeDistribution("present", "1.1"),
    ]

    assert validate_container_artifact.dependency_closure_errors(distributions) == [
        "consumer==1.0 requires missing>=1, but missing is not installed"
    ]


def test_dependency_closure_reports_incompatible_installed_versions():
    distributions = [
        FakeDistribution("consumer", "1.0", ["dependency>=2"]),
        FakeDistribution("dependency", "1.5"),
    ]

    assert validate_container_artifact.dependency_closure_errors(distributions) == [
        "consumer==1.0 requires dependency>=2, but dependency==1.5 is installed"
    ]


def test_wheel_contract_requires_exact_library_and_libscrc_declaration(monkeypatch):
    libscrc = FakeDistribution("libscrc", "1.8.1", ["wheel", "setuptools; extra == 'build'"])
    monkeypatch.setattr(validate_container_artifact.metadata, "version", lambda name: "0.47.0")
    monkeypatch.setattr(
        validate_container_artifact.metadata,
        "distribution",
        lambda name: libscrc,
    )

    validate_container_artifact.assert_wheel_contract()

    libscrc.requires = ["setuptools; extra == 'build'"]
    with pytest.raises(
        validate_container_artifact.ArtifactValidationError,
        match="libscrc must declare wheel",
    ):
        validate_container_artifact.assert_wheel_contract()


def test_artifact_policy_covers_complete_modules_payload_and_removed_tooling():
    assert set(validate_container_artifact.REQUIRED_MODULES) == {
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
    }
    assert {
        "grott.py",
        "grott.ini",
        "grott_ha.py",
        "T06NNNNXMOD.json",
        "t060103xmax3.json",
        "T06221b.json",
    }.issubset(
        {path.name for path in validate_container_artifact.REQUIRED_PAYLOADS}
    )
    assert {"gcc", "g++", "cc", "make"}.issubset(
        validate_container_artifact.FORBIDDEN_EXECUTABLES
    )
    assert "/opt/venv/bin/wheel" in {
        path.as_posix() for path in validate_container_artifact.FORBIDDEN_PATHS
    }


def test_artifact_validator_parses_every_external_layout_payload(tmp_path, monkeypatch):
    monkeypatch.setattr(validate_container_artifact, "APP_DIR", tmp_path)

    for name in ("T06NNNNXMOD.json", "t060103xmax3.json", "T06221b.json"):
        (tmp_path / name).write_text('{"layout": {}}', encoding="utf-8")
    (tmp_path / "t_bad.json").write_text('{"layout": }', encoding="utf-8")
    (tmp_path / "not-a-layout.json").write_text("not json", encoding="utf-8")

    payloads = validate_container_artifact.external_layout_payloads()

    assert {path.name for path in payloads} == {
        "T06221b.json",
        "T06NNNNXMOD.json",
        "t060103xmax3.json",
        "t_bad.json",
    }


def test_artifact_validator_has_no_optimization_disableable_assertions():
    source_path = Path(validate_container_artifact.__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"))

    assert not [node for node in ast.walk(tree) if isinstance(node, ast.Assert)]


def test_artifact_contract_failure_survives_python_optimization():
    result = subprocess.run(
        [
            sys.executable,
            "-O",
            "-c",
            (
                "from tools.validate_container_artifact import require; "
                "require(False, 'seeded optimized contract failure')"
            ),
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "seeded optimized contract failure" in result.stderr
