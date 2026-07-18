from __future__ import annotations

import ast
from dataclasses import dataclass
import json
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
    assert validate_container_artifact.APPROVED_EXTERNAL_LAYOUTS == {
        "T06NNNNXMOD.json": {"T06NNNNXMOD"},
        "t060103xmax3.json": {"T060103XMAX"},
        "T06221b.json": {"T06221b"},
    }
    assert validate_container_artifact.APPROVED_EXTERNAL_LAYOUT_SHA256 == {
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
    assert "/app/t06NNNNX.json" in {
        path.as_posix() for path in validate_container_artifact.FORBIDDEN_PATHS
    }
    generic = validate_container_artifact.EXPECTED_GENERIC_LAYOUT
    assert len(generic) == 31
    assert generic["pvpowerout"] == {
        "value": 250,
        "length": 4,
        "type": "numx",
        "divide": 10,
    }
    assert generic["pvipmtemperature"]["value"] == 546


def test_generic_layout_contract_rejects_sparse_or_semantically_changed_override():
    correct = {
        "T06NNNNX": {
            key: dict(spec)
            for key, spec in validate_container_artifact.EXPECTED_GENERIC_LAYOUT.items()
        }
    }
    validate_container_artifact.assert_generic_layout_contract(correct)

    del correct["T06NNNNX"]["pvgridcurrent"]
    correct["T06NNNNX"]["pvpowerout"]["type"] = "num"
    with pytest.raises(
        validate_container_artifact.ArtifactValidationError,
        match="generic T06NNNNX layout",
    ):
        validate_container_artifact.assert_generic_layout_contract(correct)


def test_artifact_validator_parses_every_external_layout_payload(tmp_path, monkeypatch):
    monkeypatch.setattr(validate_container_artifact, "APP_DIR", tmp_path)

    for name in ("T06NNNNXMOD.json", "t060103xmax3.json", "T06221b.json"):
        (tmp_path / name).write_text('{"layout": {}}', encoding="utf-8")
    (tmp_path / "t_bad.json").write_text('{"layout": }', encoding="utf-8")
    (tmp_path / "t_disguised.json.backup").write_text(
        '{"layout": {}}',
        encoding="utf-8",
    )
    (tmp_path / "not-a-layout.json").write_text("not json", encoding="utf-8")

    payloads = validate_container_artifact.external_layout_payloads()

    assert {path.name for path in payloads} == {
        "T06221b.json",
        "T06NNNNXMOD.json",
        "t060103xmax3.json",
        "t_bad.json",
        "t_disguised.json.backup",
    }


def test_external_layout_conflicts_report_builtin_and_duplicate_keys(tmp_path):
    first = tmp_path / "T_first.json"
    first.write_text(
        json.dumps({"T_BUILTIN": {}, "T_DUPLICATE": {}}),
        encoding="utf-8",
    )
    second = tmp_path / "t_second.json"
    second.write_text(
        json.dumps({"T_DUPLICATE": {}, "T_SAFE": {}}),
        encoding="utf-8",
    )

    assert validate_container_artifact.external_layout_conflicts(
        {"T_BUILTIN": {}},
        [second, first],
    ) == {
        "T_BUILTIN": ["T_first.json"],
        "T_DUPLICATE": ["T_first.json", "t_second.json"],
    }


def test_external_layout_contract_rejects_changed_approved_semantics(tmp_path):
    source_dir = Path(__file__).resolve().parents[1] / "examples" / "Record Layout"
    layout_paths = []
    loaded_recorddict = {}
    for name in validate_container_artifact.APPROVED_EXTERNAL_LAYOUTS:
        payload = json.loads((source_dir / name).read_text(encoding="utf-8"))
        if name == "T06NNNNXMOD.json":
            layout = payload["T06NNNNXMOD"]
            field = next(value for value in layout.values() if "divide" in value)
            field["divide"] += 1
        path = tmp_path / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        layout_paths.append(path)
        loaded_recorddict.update(payload)

    with pytest.raises(
        validate_container_artifact.ArtifactValidationError,
        match="reviewed semantic digest",
    ):
        validate_container_artifact.assert_external_layout_contract(
            {},
            loaded_recorddict,
            layout_paths,
        )


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
