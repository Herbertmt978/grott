from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest
import yaml

from tools import validate_ha_addon_repo
from tools import validate_release


ROOT = Path(__file__).resolve().parents[1]
RELEASE_PREPARED_DATE = "2026-07-18"
CURRENT_RELEASE_VERSION = "0.1.12-beta"
CURRENT_RELEASE_TAG = f"v{CURRENT_RELEASE_VERSION}"
ROLLBACK_RUNTIME_DIGEST = (
    "sha256:872ca78235cd6552b26f16dcd0de17ce18288fc92ffa82e31435c7651e619b6c"
)
ROLLBACK_ADDON_DIGEST = (
    "sha256:4446cbcfe83c00e8c667101067fc0643afded0b6338f1d6c76ba6af113c13831"
)


def canonical_release_version(root: Path = ROOT) -> str:
    config = yaml.safe_load(
        (root / "addons/grott/config.yaml").read_text(encoding="utf-8")
    )
    return str(config["version"])


def curated_release_notes_path(root: Path = ROOT) -> Path:
    return root / "docs" / "releases" / f"v{canonical_release_version(root)}.md"


def copy_release_metadata(destination: Path) -> None:
    for directory in (".github", "addons", "docker", "docs", "examples"):
        ignore = shutil.ignore_patterns("aegis") if directory == "docs" else None
        shutil.copytree(ROOT / directory, destination / directory, ignore=ignore)
    for filename in ("README.md", "grott.py", ".dockerignore"):
        shutil.copy2(ROOT / filename, destination / filename)
    releasing = ROOT / "RELEASING.md"
    if releasing.exists():
        shutil.copy2(releasing, destination / releasing.name)
    tools_dir = destination / "tools"
    tools_dir.mkdir(exist_ok=True)
    shutil.copy2(
        ROOT / "tools" / "verify_release_controls.py",
        tools_dir / "verify_release_controls.py",
    )


def test_release_preparation_targets_v012_beta() -> None:
    assert canonical_release_version() == CURRENT_RELEASE_VERSION
    assert curated_release_notes_path().name == f"{CURRENT_RELEASE_TAG}.md"


def test_current_release_metadata_is_aligned() -> None:
    config = yaml.safe_load(
        (ROOT / "addons/grott/config.yaml").read_text(encoding="utf-8")
    )
    compose = yaml.safe_load(
        (ROOT / "docker/docker-compose.yml").read_text(encoding="utf-8")
    )
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    addon_docs = (ROOT / "addons/grott/DOCS.md").read_text(encoding="utf-8")
    release_version = canonical_release_version()

    assert re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", release_version)
    assert config["stage"] == "experimental"
    assert compose["services"]["grott"]["image"] == (
        f"ghcr.io/herbertmt978/grott:{release_version}"
    )
    assert f"current beta line is `v{release_version}`" in readme
    assert f"docs/releases/v{release_version}.md" in readme
    assert f"ghcr.io/herbertmt978/grott:{release_version}" in readme
    assert f"Current beta line: `{release_version}`" in addon_docs
    assert "latest supported release is `v0.1.9-beta`" in readme
    assert "`v0.1.10-beta` and `v0.1.11-beta` remain published prereleases" in readme
    assert "owner explicitly waived the remaining observation window" in readme
    assert "Releases page is the supported-availability authority" in readme
    assert "does not prove that GHCR tags are absent" in readme
    assert "pre-publication examples only" in readme
    lifecycle_docs = f"{readme}\n{addon_docs}".lower()
    assert not any(
        phrase in lifecycle_docs
        for phrase in validate_release.STALE_WAIVER_LIFECYCLE_PHRASES
    )


@pytest.mark.parametrize(
    "stale_wording",
    validate_release.STALE_WAIVER_LIFECYCLE_PHRASES,
)
def test_release_validator_rejects_stale_waiver_lifecycle_wording(
    tmp_path: Path, stale_wording: str
) -> None:
    copy_release_metadata(tmp_path)
    readme_path = tmp_path / "README.md"
    readme_path.write_text(
        readme_path.read_text(encoding="utf-8") + f"\n{stale_wording}\n",
        encoding="utf-8",
    )

    errors = validate_release.validate_worktree(tmp_path)

    assert any("stale pre-publication lifecycle wording" in error for error in errors)


def test_changelog_has_empty_unreleased_then_prepared_candidate() -> None:
    changelog = (ROOT / "addons/grott/CHANGELOG.md").read_text(encoding="utf-8")
    release_version = canonical_release_version()

    assert re.search(
        rf"^## Unreleased\s*^## {re.escape(release_version)} - prepared {RELEASE_PREPARED_DATE}$",
        changelog,
        re.MULTILINE,
    )
    release_section = changelog.split(
        f"## {release_version} - prepared {RELEASE_PREPARED_DATE}", 1
    )[1].split("\n## ", 1)[0]
    assert "reviewed three-file allowlist" in release_section
    assert "built-in layouts" in release_section
    assert "fixture" in release_section.lower()
    assert "not a claim that the beta has been published" in release_section


def test_support_docs_require_packet_identifier_pseudonymisation() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    addon_docs = (ROOT / "addons/grott/DOCS.md").read_text(encoding="utf-8")

    assert "never post raw packet hex unchanged" in readme
    assert "stable device identifiers" in readme
    assert "Always pseudonymise serial numbers" in addon_docs


def test_curated_release_notes_are_human_written_and_versioned() -> None:
    path = curated_release_notes_path()

    assert path.is_file(), f"missing curated release notes: {path.relative_to(ROOT)}"
    payload = path.read_bytes()
    assert payload.endswith(b"\n")
    assert b"\r" not in payload
    assert b"\x00" not in payload

    text = payload.decode("utf-8")
    release_version = canonical_release_version()
    assert text.startswith(f"# Grott {release_version}\n")
    assert "## Immutable release images" not in text
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "docs/releases/*.md text eol=lf" in attributes
    for label in (
        "**What was the problem?**",
        "**What did we do?**",
        "**Why was it a problem?**",
        "**Benefit to users:**",
    ):
        assert text.count(label) >= 6
    for user_topic in (
        "layout",
        "forwarding",
        "Home Assistant",
        "configuration",
        "containers",
        "release process",
        "XML",
    ):
        assert user_topic.lower() in text.lower()


def test_v012_release_notes_cover_layout_packaging_and_safe_upgrade_contract() -> None:
    path = ROOT / "docs" / "releases" / f"{CURRENT_RELEASE_TAG}.md"
    assert path.is_file(), f"missing {path.relative_to(ROOT)}"
    text = path.read_text(encoding="utf-8")
    normalized = text.lower()

    for required in (
        "packaged example layouts",
        "sph battery soc",
        "64 as 0.64",
        "min",
        "tl3",
        "t060120",
        "reviewed three-file allowlist",
        "final-image validator",
        "fixture/container tested",
        "not real-hardware tested",
        "32 entities",
        "v0_1_9_standard",
        "retained discovery cleanup",
        "upgrade",
        "rollback",
        "supported-availability authority",
        "omitted option",
        "next live packet",
        "image rollback does not restore",
        "home assistant repairs",
        "changes carried forward from v0.1.11-beta",
        "tcp frame reassembly",
        "safe literal",
        "non-root",
        "exact-source annotated tags",
    ):
        assert required in normalized
    assert "has not been published" not in normalized


def test_entity_profile_docs_distinguish_generic_and_mod_all_counts() -> None:
    for path in (ROOT / "README.md", ROOT / "addons/grott/DOCS.md"):
        text = path.read_text(encoding="utf-8")
        assert "T06NNNNXMOD" in text and "171 discovered entities" in text
        assert "T06NNNNX` remains at the verified 32 entities" in text


def test_packaged_release_is_honest_about_proxy_only_mode_support() -> None:
    notes = curated_release_notes_path().read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "moved that setup to the standard Docker image" not in notes
    assert "packaged Docker profiles support proxy mode only" in notes
    assert "do not upgrade either packaged image" in notes
    assert f"{CURRENT_RELEASE_TAG} images and supplied Compose profile" in readme
    assert "qualified for proxy mode only" in readme


@pytest.mark.parametrize(
    ("payload", "expected_error"),
    (
        (None, "curated release notes"),
        (b"   \n", "curated release notes"),
        (b"# Notes\r\n", "LF-only"),
        (b"# Notes\x00\n", "NUL"),
        (b"# Notes\n\n## Immutable release images\n", "reserved"),
    ),
)
def test_release_validator_rejects_invalid_curated_notes(
    tmp_path: Path,
    payload: bytes | None,
    expected_error: str,
) -> None:
    copy_release_metadata(tmp_path)
    path = curated_release_notes_path(tmp_path)
    if path.exists():
        path.unlink()
    if payload is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    errors = validate_release.validate_worktree(tmp_path)

    assert any(expected_error.lower() in error.lower() for error in errors), errors


@pytest.mark.parametrize(
    "required_token",
    (
        "supported-availability authority",
        "reviewed three-file allowlist",
        "Changes carried forward from v0.1.11-beta",
        "next live packet",
        "Home Assistant Repairs",
    ),
)
def test_release_validator_rejects_incomplete_public_notes(
    tmp_path: Path, required_token: str
) -> None:
    copy_release_metadata(tmp_path)
    path = curated_release_notes_path(tmp_path)
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace(required_token, "removed-token"), encoding="utf-8", newline="\n")

    errors = validate_release.validate_worktree(tmp_path)

    assert any("curated release notes" in error.lower() for error in errors), errors


def test_current_docs_distinguish_the_three_version_domains() -> None:
    release_version = canonical_release_version()
    for path in (ROOT / "README.md", ROOT / "addons/grott/DOCS.md"):
        text = path.read_text(encoding="utf-8")
        assert "Fork/add-on release" in text and release_version in text
        assert "Bundled Grott core (upstream startup version)" in text
        assert "2.8.3" in text
        assert "Bundled Home Assistant extension" in text and "0.0.8" in text


def test_operator_docs_cover_runtime_filesystem_constraints_and_rollback() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    addon_docs = (ROOT / "addons/grott/DOCS.md").read_text(encoding="utf-8")
    releasing_path = ROOT / "RELEASING.md"

    assert releasing_path.exists(), "RELEASING.md must define the release runbook"
    releasing = releasing_path.read_text(encoding="utf-8")
    assert "UID/GID `10001:10001`" in readme
    assert "readable by UID `10001`" in readme
    assert "read_only: false" in readme
    assert "optional file output" in readme
    assert "Backup" in addon_docs and "grott_last_push" in addon_docs
    assert "ShinePhone" in addon_docs
    for text in (readme, releasing):
        assert "Docker-backed Home Assistant" in text
        assert "retained MQTT" in text
        assert "Home Assistant Repairs" in text
    for text in (readme, addon_docs, releasing):
        assert "Grott pre-update rollback" in text
        assert "UAT must not begin" in text
        assert not re.search(
            r"reinstall(?:ing)?(?: the)? add-on(?: version)? `?0\.1\.9-beta",
            text,
        )
    for text in (readme, addon_docs, releasing):
        assert "0.1.11-beta" in text
    assert ROLLBACK_RUNTIME_DIGEST in readme
    assert ROLLBACK_ADDON_DIGEST in addon_docs
    assert ROLLBACK_RUNTIME_DIGEST in releasing
    assert ROLLBACK_ADDON_DIGEST in releasing


def test_release_runbook_records_every_hard_gate_and_recovery_path() -> None:
    path = ROOT / "RELEASING.md"
    assert path.exists(), "RELEASING.md must define the release runbook"
    text = path.read_text(encoding="utf-8")

    for required in (
        "upstream redistribution permission has been obtained",
        "Preserve the permission record outside this repository",
        "does not authorize commercial use or reuse unless Johan Meijer",
        "financial reward or appreciation is directed to him",
        "protected default branch",
        "protected `release` environment",
        "protected `v*` tag ruleset",
        "hosted CI",
        "Home Assistant UAT",
        "workflow_dispatch",
        "digest",
        f"docs/releases/{CURRENT_RELEASE_TAG}.md",
        "human-written",
        "exact source SHA",
        "exact full body",
        "linux/amd64",
        "linux/arm64",
        "linux/arm/v7",
        "linux/386",
        "Idempotent retry",
        "Recovery",
        "Rollback",
    ):
        assert required in text


def test_legal_status_records_permission_and_commercial_use_limit() -> None:
    legal = (ROOT / "docs/LEGAL.md").read_text(encoding="utf-8")

    assert "upstream redistribution permission has been obtained" in legal
    assert "Preserve the permission record outside this repository" in legal
    assert "does not authorize commercial use or reuse unless Johan Meijer" in legal
    assert "financial reward or appreciation is directed to him" in legal
    assert "Public release still requires" in legal
    assert "Local/private testing" in legal
    assert "Publish Home Assistant and Docker images" not in legal
    assert "Redistribution permission alone does not authorize relicensing" in legal

    public_docs = (
        ROOT / "README.md",
        ROOT / "addons/grott/DOCS.md",
        ROOT / "RELEASING.md",
        ROOT / "docs/LEGAL.md",
        ROOT / "docs/releases" / f"{CURRENT_RELEASE_TAG}.md",
    )
    for path in public_docs:
        text = path.read_text(encoding="utf-8")
        assert "redistribution permission has been obtained" in text
        assert "does not authorize commercial use or reuse unless Johan Meijer" in text
        assert "financial reward or appreciation is directed to him" in text
        assert "No public redistribution release may be made until" not in text


def test_release_validator_rejects_incomplete_layout_allowlist(tmp_path: Path) -> None:
    copy_release_metadata(tmp_path)
    path = tmp_path / ".dockerignore"
    text = path.read_text(encoding="utf-8").replace(
        "!/examples/Record Layout/T06221b.json\n", "", 1
    )
    path.write_text(text, encoding="utf-8")

    errors = validate_release.validate_worktree(tmp_path)

    assert any("reviewed layout allowlist" in error.lower() for error in errors), errors


def test_release_validator_rejects_bulk_external_layout_copy(tmp_path: Path) -> None:
    copy_release_metadata(tmp_path)
    path = tmp_path / "docker" / "dockerfile"
    text = path.read_text(encoding="utf-8").replace(
        validate_release.REVIEWED_LAYOUT_COPY,
        'COPY ["examples/Record Layout/", "/app/"]',
        1,
    )
    path.write_text(text, encoding="utf-8")

    errors = validate_release.validate_worktree(tmp_path)

    assert any("reviewed external layout allowlist" in error for error in errors), errors


def test_release_validator_rejects_add_and_broad_context_reinclude(
    tmp_path: Path,
) -> None:
    copy_release_metadata(tmp_path)
    path = tmp_path / "docker" / "dockerfile"
    path.write_text(
        path.read_text(encoding="utf-8")
        + '\nADD ["examples/Record Layout/", "/app/"]\n',
        encoding="utf-8",
    )
    dockerignore_path = tmp_path / ".dockerignore"
    dockerignore_path.write_text(
        dockerignore_path.read_text(encoding="utf-8") + "\n!/examples/**\n",
        encoding="utf-8",
    )

    errors = validate_release.validate_worktree(tmp_path)

    assert any("Dockerfile ADD" in error for error in errors), errors
    assert any("reviewed layout allowlist" in error for error in errors), errors


def test_issue_template_version_examples_are_version_neutral() -> None:
    template_paths = sorted((ROOT / ".github/ISSUE_TEMPLATE").glob("*.yml"))
    assert template_paths
    version_example = re.compile(
        r"placeholder:\s*['\"]?v?\d+\.\d+\.(?:\d+|[xX])"
    )

    stale = [
        path.name
        for path in template_paths
        if version_example.search(path.read_text(encoding="utf-8"))
    ]
    assert not stale, f"version-specific issue placeholders: {stale}"


def test_ha_validator_accepts_any_semverish_candidate_version(
    monkeypatch,
) -> None:
    config = yaml.safe_load(
        (ROOT / "addons/grott/config.yaml").read_text(encoding="utf-8")
    )
    config["version"] = "9.9.9-beta"
    config["stage"] = "experimental"
    monkeypatch.setattr(validate_ha_addon_repo, "load_yaml", lambda _path: config)
    errors: list[str] = []

    validate_ha_addon_repo.validate_addon_config(errors)

    assert not any("version" in error.lower() for error in errors)


def test_ha_validator_requires_experimental_stage(monkeypatch) -> None:
    config = yaml.safe_load(
        (ROOT / "addons/grott/config.yaml").read_text(encoding="utf-8")
    )
    config["stage"] = "stable"
    monkeypatch.setattr(validate_ha_addon_repo, "load_yaml", lambda _path: config)
    errors: list[str] = []

    validate_ha_addon_repo.validate_addon_config(errors)

    assert any("experimental" in error for error in errors)


def test_release_validator_rejects_compose_version_drift(tmp_path: Path) -> None:
    copy_release_metadata(tmp_path)
    release_version = canonical_release_version(tmp_path)
    compose_path = tmp_path / "docker/docker-compose.yml"
    compose = compose_path.read_text(encoding="utf-8").replace(
        f"grott:{release_version}", "grott:9.9.9-beta"
    )
    compose_path.write_text(compose, encoding="utf-8")

    errors = validate_release.validate_worktree(tmp_path)

    assert any("Compose runtime image" in error for error in errors)


def test_release_validator_requires_named_verified_ha_backup(tmp_path: Path) -> None:
    copy_release_metadata(tmp_path)
    readme_path = tmp_path / "README.md"
    readme_path.write_text(
        readme_path.read_text(encoding="utf-8").replace(
            "Grott pre-update rollback", "an unnamed backup"
        ),
        encoding="utf-8",
    )

    errors = validate_release.validate_worktree(tmp_path)

    assert any("named verified Home Assistant backup" in error for error in errors)


def test_release_validator_rejects_historical_addon_reinstall_rollback(
    tmp_path: Path,
) -> None:
    copy_release_metadata(tmp_path)
    addon_docs_path = tmp_path / "addons/grott/DOCS.md"
    addon_docs_path.write_text(
        addon_docs_path.read_text(encoding="utf-8")
        + "\nRollback by reinstalling add-on version `0.1.9-beta`.\n",
        encoding="utf-8",
    )

    errors = validate_release.validate_worktree(tmp_path)

    assert any("historical add-on reinstall" in error for error in errors)


def test_release_validator_derives_current_version_from_addon_config(
    tmp_path: Path,
) -> None:
    copy_release_metadata(tmp_path)
    old_version = canonical_release_version(tmp_path)
    new_version = "9.8.7-beta"
    config_path = tmp_path / "addons/grott/config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["version"] = new_version
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    old_notes_path = tmp_path / "docs" / "releases" / f"v{old_version}.md"
    new_notes_path = tmp_path / "docs" / "releases" / f"v{new_version}.md"
    new_notes_path.write_text(
        old_notes_path.read_text(encoding="utf-8").replace(old_version, new_version),
        encoding="utf-8",
        newline="\n",
    )
    old_notes_path.unlink()
    for relative_path in (
        "README.md",
        "RELEASING.md",
        "addons/grott/DOCS.md",
        "addons/grott/CHANGELOG.md",
        "docker/docker-compose.yml",
    ):
        path = tmp_path / relative_path
        path.write_text(
            path.read_text(encoding="utf-8").replace(old_version, new_version),
            encoding="utf-8",
        )

    errors = validate_release.validate_worktree(tmp_path, f"v{new_version}")

    assert not errors


def test_release_validator_rejects_nonempty_unreleased_section(tmp_path: Path) -> None:
    copy_release_metadata(tmp_path)
    changelog_path = tmp_path / "addons/grott/CHANGELOG.md"
    changelog = changelog_path.read_text(encoding="utf-8").replace(
        "## Unreleased\n\n", "## Unreleased\n\n- Not yet released.\n\n", 1
    )
    changelog_path.write_text(changelog, encoding="utf-8")

    errors = validate_release.validate_worktree(tmp_path)

    assert any("Unreleased section must be empty" in error for error in errors)


def test_release_validator_rejects_missing_publication_gate(tmp_path: Path) -> None:
    copy_release_metadata(tmp_path)
    releasing_path = tmp_path / "RELEASING.md"
    if releasing_path.exists():
        text = releasing_path.read_text(encoding="utf-8").replace(
            "upstream redistribution permission has been obtained",
            "upstream permission is assumed",
            1,
        )
        releasing_path.write_text(text, encoding="utf-8")

    errors = validate_release.validate_worktree(tmp_path)

    assert any("permission" in error for error in errors)
