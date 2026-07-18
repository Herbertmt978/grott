#!/usr/bin/env python3
"""Validate static release invariants before Grott artifacts are published."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode


DEFAULT_ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = Path(".github/workflows/ci.yml")
PUBLISH_WORKFLOW = Path(".github/workflows/publish-ghcr.yml")
WORKFLOW_PATHS = (CI_WORKFLOW, PUBLISH_WORKFLOW)
RELEASE_CONTROL_TOOL = Path("tools/verify_release_controls.py")
REQUIRED_PLATFORMS = {
    "linux/amd64",
    "linux/arm64",
    "linux/arm/v7",
    "linux/386",
}
EXPECTED_BUILD_FILES = {"docker/dockerfile", "addons/grott/Dockerfile"}
EXPECTED_BUILD_IDS = {
    "docker/dockerfile": "runtime_build",
    "addons/grott/Dockerfile": "addon_build",
}
REVIEWED_LAYOUT_COPY = (
    'COPY ["examples/Record Layout/T06NNNNXMOD.json", '
    '"examples/Record Layout/t060103xmax3.json", '
    '"examples/Record Layout/T06221b.json", "/app/"]'
)
REVIEWED_LAYOUT_CONTEXT_LINES = (
    "/examples/Record Layout/*",
    "!/examples/Record Layout/T06NNNNXMOD.json",
    "!/examples/Record Layout/t060103xmax3.json",
    "!/examples/Record Layout/T06221b.json",
)
REVIEWED_DOCKERIGNORE_NEGATIONS = (
    "!/examples/Record Layout/T06NNNNXMOD.json",
    "!/examples/Record Layout/t060103xmax3.json",
    "!/examples/Record Layout/T06221b.json",
    "!/examples/grott.ini",
    "!/.env.example",
)
EXPECTED_RELEASE_PREPARED_DATE = "2026-07-18"
EXPECTED_GROTT_STARTUP_VERSION = "2.8.3"
EXPECTED_HA_EXTENSION_VERSION = "0.0.8"
EXPECTED_RUNTIME_IMAGE = "ghcr.io/herbertmt978/grott"
EXPECTED_ADDON_IMAGE = "ghcr.io/herbertmt978/grott-ha-docker"
STALE_WAIVER_LIFECYCLE_PHRASES = (
    "current candidate is being tested",
    "must not be published until the vm soak",
    "intentionally unavailable during local uat",
    "unpublished candidate add-on metadata",
    "this release is beta software. it is being tested",
)
ROLLBACK_RUNTIME_DIGEST = (
    "sha256:066d806774a147bc4c448761d026eb831cdcfa29bc32ef3a1c361a36a2ea361a"
)
ROLLBACK_ADDON_DIGEST = (
    "sha256:410f2b2e4dfe810aa1d9d8b8591eaae0852ae9f61486d78f663cd6a95c2ab6f1"
)
PUBLISH_STEP_SEQUENCE = (
    "Check out validated source SHA",
    "Set up QEMU",
    "Set up Docker Buildx",
    "Revalidate remote tag before package login",
    "Log in to GHCR",
    "Build and stage runtime candidate",
    "Build and stage Home Assistant add-on candidate",
    "Verify candidate manifests by digest",
    "Validate and scan every staged candidate platform",
    "Guard final tags against conflicting digests",
    "Revalidate remote tag before final promotion",
    "Promote verified digests to release tags",
    "Verify promoted release tags",
)
PINNED_TRIVY_IMAGE = (
    "aquasec/trivy:0.72.0@"
    "sha256:cffe3f5161a47a6823fbd23d985795b3ed72a4c806da4c4df16266c02accdd6f"
)
PUBLISH_ACTION_PREFIXES = {
    "Check out validated source SHA": "actions/checkout@",
    "Set up QEMU": "docker/setup-qemu-action@",
    "Set up Docker Buildx": "docker/setup-buildx-action@",
    "Log in to GHCR": "docker/login-action@",
    "Build and stage runtime candidate": "docker/build-push-action@",
    "Build and stage Home Assistant add-on candidate": "docker/build-push-action@",
}
EXPECTED_RELEASE_CONTROL_TOOL_SHA256 = (
    "49c966ce0c7851ebf6c1b1c6027beeeddf1516281b5085968f6dc86856645430"
)
CANONICAL_PUBLISH_WORKFLOW_SHA256 = "ffe2720b3da9a38868e46f6af733fa7b45e020973eed3a80cbe9fb34bb426699"
PINNED_ACTION_RE = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")
INPUT_TAG_EXPRESSION_RE = re.compile(r"\$\{\{\s*inputs\.tag\s*}}")
TAG_RE = re.compile(
    r"^v(0|[1-9][0-9]*)\."
    r"(0|[1-9][0-9]*)\."
    r"(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


class UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys at every depth."""

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[Any, Any]:
        if not isinstance(node, MappingNode):
            raise ConstructorError(
                None,
                None,
                f"expected a mapping node, but found {node.id}",
                node.start_mark,
            )
        self.flatten_mapping(node)
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                hash(key)
            except TypeError as exc:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "found an unhashable mapping key",
                    key_node.start_mark,
                ) from exc
            if key in mapping:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"duplicate mapping key {key!r}",
                    key_node.start_mark,
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


# GitHub Actions uses YAML 1.2 semantics, where `on` is a string rather than a
# YAML 1.1 boolean. Keep SafeLoader's constructors while narrowing bool parsing.
UniqueKeySafeLoader.yaml_implicit_resolvers = copy.deepcopy(yaml.SafeLoader.yaml_implicit_resolvers)
for first_character, resolvers in UniqueKeySafeLoader.yaml_implicit_resolvers.items():
    UniqueKeySafeLoader.yaml_implicit_resolvers[first_character] = [resolver for resolver in resolvers if resolver[0] != "tag:yaml.org,2002:bool"]
UniqueKeySafeLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)


def load_mapping(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as handle:
            value = yaml.load(handle, Loader=UniqueKeySafeLoader)
    except FileNotFoundError as exc:
        raise ValueError(f"required release file is missing: {path}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return value


def require(errors: list[str], condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


def read_required_text(root: Path, relative_path: Path, errors: list[str]) -> str:
    path = root / relative_path
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        errors.append(f"required release file is unreadable: {path}: {exc}")
        return ""


def extract_assignment(text: str, name: str) -> str | None:
    match = re.search(
        rf"^{re.escape(name)}\s*=\s*['\"]([^'\"]+)['\"]\s*$",
        text,
        re.MULTILINE,
    )
    return match.group(1) if match else None


def validate_release_metadata(root: Path, errors: list[str]) -> None:
    release_control_path = root / RELEASE_CONTROL_TOOL
    try:
        release_control_payload = release_control_path.read_bytes()
    except OSError as exc:
        errors.append(
            f"release-control verifier is missing or unreadable: "
            f"{release_control_path}: {exc}"
        )
    else:
        require(
            errors,
            hashlib.sha256(release_control_payload).hexdigest()
            == EXPECTED_RELEASE_CONTROL_TOOL_SHA256,
            "release-control verifier does not match its reviewed SHA-256",
        )

    config_path = root / "addons" / "grott" / "config.yaml"
    try:
        addon_config = load_mapping(config_path)
    except ValueError as exc:
        errors.append(str(exc))
        addon_config = {}

    addon_version = str(addon_config.get("version", ""))
    require(
        errors,
        validate_tag_syntax(f"v{addon_version}"),
        "add-on config version must be a valid release version",
    )
    require(
        errors,
        addon_config.get("stage") == "experimental",
        "add-on stage must remain experimental until every release gate is satisfied",
    )

    curated_notes_path = root / "docs" / "releases" / f"v{addon_version}.md"
    try:
        curated_notes_payload = curated_notes_path.read_bytes()
    except OSError as exc:
        errors.append(
            f"curated release notes are missing or unreadable: {curated_notes_path}: {exc}"
        )
        curated_notes_payload = b""
    if curated_notes_payload:
        require(
            errors,
            b"\r" not in curated_notes_payload,
            "curated release notes must use LF-only line endings",
        )
        require(
            errors,
            b"\x00" not in curated_notes_payload,
            "curated release notes must not contain NUL bytes",
        )
        require(
            errors,
            curated_notes_payload.endswith(b"\n"),
            "curated release notes must end with a newline",
        )
        try:
            curated_notes = curated_notes_payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            errors.append(f"curated release notes must be valid UTF-8: {exc}")
            curated_notes = ""
        require(
            errors,
            bool(curated_notes.strip()),
            "curated release notes are missing or blank",
        )
        require(
            errors,
            "## Immutable release images" not in curated_notes,
            "curated release notes must not contain the reserved immutable image heading",
        )
        curated_lower = curated_notes.lower()
        required_note_tokens = (
            "supported-availability authority",
            "reviewed three-file allowlist",
            "sph battery soc",
            "fixture/container tested",
            "not real-hardware tested",
            "v0_1_9_standard",
            "omitted option",
            "next live packet",
            "image rollback does not restore",
            "home assistant repairs",
            "changes carried forward from v0.1.11-beta",
            "tcp frame reassembly",
            "safe literal",
            "non-root",
            "exact-source annotated tags",
            "stable promotion",
            "runtime and layout behavior",
            "upstream redistribution permission has been obtained",
            "does not authorize commercial use or reuse unless johan meijer",
            "financial reward or appreciation is directed to him",
        )
        require(
            errors,
            all(token in curated_lower for token in required_note_tokens)
            and "has not been published" not in curated_lower,
            "curated release notes must be cumulative, publication-neutral, and cover automatic cleanup, UAT, and permission boundaries",
        )
    else:
        require(
            errors,
            False,
            "curated release notes are missing or blank",
        )

    compose_path = root / "docker" / "docker-compose.yml"
    try:
        compose = load_mapping(compose_path)
    except ValueError as exc:
        errors.append(str(exc))
        compose = {}
    services = compose.get("services") if isinstance(compose, dict) else None
    grott_service = services.get("grott") if isinstance(services, dict) else None
    compose_image = grott_service.get("image") if isinstance(grott_service, dict) else None
    require(
        errors,
        compose_image == f"{EXPECTED_RUNTIME_IMAGE}:{addon_version}",
        "Compose runtime image must use the current fork release version",
    )

    texts = {
        "README.md": read_required_text(root, Path("README.md"), errors),
        "addons/grott/DOCS.md": read_required_text(
            root, Path("addons/grott/DOCS.md"), errors
        ),
        "addons/grott/CHANGELOG.md": read_required_text(
            root, Path("addons/grott/CHANGELOG.md"), errors
        ),
        "RELEASING.md": read_required_text(root, Path("RELEASING.md"), errors),
        "docs/LEGAL.md": read_required_text(root, Path("docs/LEGAL.md"), errors),
        ".dockerignore": read_required_text(root, Path(".dockerignore"), errors),
        "docker/dockerfile": read_required_text(
            root,
            Path("docker/dockerfile"),
            errors,
        ),
        "addons/grott/Dockerfile": read_required_text(
            root,
            Path("addons/grott/Dockerfile"),
            errors,
        ),
    }
    readme = texts["README.md"]
    addon_docs = texts["addons/grott/DOCS.md"]
    changelog = texts["addons/grott/CHANGELOG.md"]
    releasing = texts["RELEASING.md"]
    legal = texts["docs/LEGAL.md"]
    dockerignore = texts[".dockerignore"]

    require(
        errors,
        f"current stable release line is `v{addon_version}`" in readme
        and f"{EXPECTED_RUNTIME_IMAGE}:{addon_version}" in readme
        and f"`v{addon_version}` is the repository Latest release" in readme
        and "`v0.1.12-beta` remains an immutable historical prerelease" in readme
        and "owner explicitly waived the remaining observation window" in readme
        and "Releases page is the supported-availability authority" in readme
        and "does not prove that GHCR tags are absent" in readme
        and "supported only when its matching release entry exists" in readme,
        "README current release and install image must match the release candidate",
    )
    require(
        errors,
        f"Current stable release line: `{addon_version}`" in addon_docs,
        "add-on operator docs must identify the current stable release line",
    )
    lifecycle_docs = f"{readme}\n{addon_docs}".lower()
    require(
        errors,
        not any(
            phrase in lifecycle_docs for phrase in STALE_WAIVER_LIFECYCLE_PHRASES
        ),
        "recorded UAT waiver must not coexist with stale pre-publication lifecycle wording",
    )
    dockerignore_lines = [line.strip() for line in dockerignore.splitlines()]
    layout_context_lines = [
        line.strip()
        for line in dockerignore_lines
        if line.strip().startswith("/examples/Record Layout/")
        or line.strip().startswith("!/examples/Record Layout/")
    ]
    require(
        errors,
        layout_context_lines == list(REVIEWED_LAYOUT_CONTEXT_LINES),
        "Docker build context must use the exact reviewed layout allowlist",
    )
    require(
        errors,
        [line for line in dockerignore_lines if line.startswith("!")]
        == list(REVIEWED_DOCKERIGNORE_NEGATIONS),
        "Docker build context negations must match the reviewed layout allowlist",
    )
    for dockerfile_path in ("docker/dockerfile", "addons/grott/Dockerfile"):
        dockerfile_lines = [
            line.strip() for line in texts[dockerfile_path].splitlines()
        ]
        require(
            errors,
            not any(
                re.match(r"(?i)^ADD(?:\s|\[)", line)
                for line in dockerfile_lines
            ),
            f"{dockerfile_path}: Dockerfile ADD instructions are forbidden",
        )
        layout_reference_lines = [
            line.strip()
            for line in dockerfile_lines
            if re.search(r"record(?:\s|\\)+layout", line, re.IGNORECASE)
        ]
        require(
            errors,
            layout_reference_lines == [REVIEWED_LAYOUT_COPY],
            f"{dockerfile_path} must use the exact reviewed external layout allowlist",
        )

    version_tokens = (
        ("Fork/add-on release", addon_version),
        ("Bundled Grott core (upstream startup version)", EXPECTED_GROTT_STARTUP_VERSION),
        ("Bundled Home Assistant extension", EXPECTED_HA_EXTENSION_VERSION),
    )
    for path, text in (
        ("README.md", readme),
        ("addons/grott/DOCS.md", addon_docs),
        ("RELEASING.md", releasing),
    ):
        require(
            errors,
            all(label in text and version in text for label, version in version_tokens),
            f"{path} must distinguish fork, Grott startup, and Home Assistant extension versions",
        )

    grott_source = read_required_text(root, Path("grott.py"), errors)
    grott_example_source = read_required_text(
        root, Path("examples/grotttest.py"), errors
    )
    extension_source = read_required_text(
        root, Path("examples/Home Assistent/grott_ha.py"), errors
    )
    require(
        errors,
        extract_assignment(grott_source, "verrel") == EXPECTED_GROTT_STARTUP_VERSION,
        f"grott.py startup version must remain {EXPECTED_GROTT_STARTUP_VERSION}",
    )
    require(
        errors,
        extract_assignment(grott_example_source, "verrel")
        == EXPECTED_GROTT_STARTUP_VERSION,
        f"examples/grotttest.py version must remain {EXPECTED_GROTT_STARTUP_VERSION}",
    )
    require(
        errors,
        extract_assignment(extension_source, "__version__")
        == EXPECTED_HA_EXTENSION_VERSION,
        f"bundled Home Assistant extension version must remain {EXPECTED_HA_EXTENSION_VERSION}",
    )

    unreleased = re.search(
        r"^## Unreleased[^\n]*\n(?P<body>.*?)(?=^## |\Z)",
        changelog,
        re.MULTILINE | re.DOTALL,
    )
    require(errors, unreleased is not None, "changelog must begin with an Unreleased section")
    require(
        errors,
        unreleased is not None and not unreleased.group("body").strip(),
        "changelog Unreleased section must be empty for the frozen release candidate",
    )
    require(
        errors,
        f"## {addon_version} - prepared {EXPECTED_RELEASE_PREPARED_DATE}" in changelog
        and "preparation date does not by itself claim publication" in changelog,
        "changelog must identify the prepared candidate without claiming publication",
    )

    require(
        errors,
        "UID/GID `10001:10001`" in readme
        and "readable by UID `10001`" in readme
        and "read_only: false" in readme
        and "optional file output" in readme,
        "README must document non-root config permissions and the file-output escape hatch",
    )
    require(
        errors,
        "Backup" in addon_docs
        and "grott_last_push" in addon_docs
        and "ShinePhone" in addon_docs,
        "add-on docs must define backup, telemetry, and ShinePhone UAT checks",
    )
    require(
        errors,
        "never post raw packet hex unchanged" in readme
        and "Always pseudonymise serial numbers" in addon_docs,
        "support docs must require packet and stable-identifier pseudonymisation",
    )

    for path, text in (
        ("README.md", readme),
        ("addons/grott/DOCS.md", addon_docs),
        ("RELEASING.md", releasing),
    ):
        require(
            errors,
            "Grott pre-update rollback" in text
            and "UAT must not begin" in text
            and re.search(
                r"only supported(?: Home Assistant)? rollback(?: path)?\b",
                text,
                re.IGNORECASE,
            )
            is not None,
            f"{path} must require the named verified Home Assistant backup as the only supported rollback path",
        )

    rollback_docs = readme + "\n" + addon_docs + "\n" + releasing
    unsupported_ha_reinstall = re.compile(
        r"\breinstall(?:ing)?(?:\s+the)?\s+add-on(?:\s+version)?\s+`?\d+\.\d+\.\d+-beta`?",
        re.IGNORECASE,
    )
    require(
        errors,
        unsupported_ha_reinstall.search(rollback_docs) is None,
        "rollback docs must not recommend historical add-on reinstall from the current repository",
    )
    require(
        errors,
        "0.1.12-beta" in readme
        and "0.1.12-beta" in addon_docs
        and "0.1.12-beta" in releasing
        and ROLLBACK_RUNTIME_DIGEST in readme
        and ROLLBACK_RUNTIME_DIGEST in releasing
        and ROLLBACK_ADDON_DIGEST in addon_docs
        and ROLLBACK_ADDON_DIGEST in releasing,
        "rollback docs must use the independently verified 0.1.12-beta manifests",
    )
    require(
        errors,
        "ledidobe/grott" not in rollback_docs,
        "current rollback guidance must prefer the verified fork images, not a third-party image",
    )

    release_gate_tokens = (
        "upstream redistribution permission has been obtained",
        "Preserve the permission record outside this repository",
        "does not authorize commercial use or reuse unless Johan Meijer",
        "financial reward or appreciation is directed to him",
        "protected default branch",
        "protected `release` environment",
        "protected `v*` tag ruleset",
        "hosted CI",
        "Home Assistant UAT",
        "Home Assistant Repairs",
        "Docker-backed Home Assistant",
        "retained MQTT",
        "workflow_dispatch",
        'RELEASE_REPO="Herbertmt978/grott"',
        '--repo "${RELEASE_REPO}"',
        "bypass actors",
        "administrators are not allowed to bypass",
        "verify_release_controls.py",
        "Idempotent retry",
        "Recovery",
        "Rollback",
    )
    require(
        errors,
        all(token in releasing for token in release_gate_tokens),
        "RELEASING.md is missing a required release, recovery, permission, or commercial-use gate",
    )
    require(
        errors,
        "upstream redistribution permission has been obtained" in legal
        and "Preserve the permission record outside this repository" in legal
        and "does not authorize commercial use or reuse unless Johan Meijer" in legal
        and "financial reward or appreciation is directed to him" in legal
        and "Redistribution permission alone does not authorize relicensing" in legal
        and "Public release still requires" in legal
        and "Local/private testing" in legal
        and "Publish Home Assistant and Docker images" not in legal,
        "docs/LEGAL.md must record redistribution permission and commercial-use limits while preserving release gates",
    )

    issue_dir = root / ".github" / "ISSUE_TEMPLATE"
    try:
        issue_templates = sorted(issue_dir.glob("*.yml"))
    except OSError as exc:
        errors.append(f"issue template directory is unreadable: {issue_dir}: {exc}")
        issue_templates = []
    version_placeholder = re.compile(
        r"placeholder:\s*['\"]?v?\d+\.\d+\.(?:\d+|[xX])"
    )
    stale_templates = [
        path.name
        for path in issue_templates
        if version_placeholder.search(path.read_text(encoding="utf-8"))
    ]
    require(errors, bool(issue_templates), "release repository must provide issue templates")
    require(
        errors,
        not stale_templates,
        f"issue template placeholders must be version-neutral: {stale_templates}",
    )


def workflow_trigger(workflow: dict[str, Any]) -> Any:
    # PyYAML 1.1 parses the unquoted key `on` as boolean true.
    return workflow.get("on", workflow.get(True))


def job_steps(job: Any) -> list[dict[str, Any]]:
    if not isinstance(job, dict) or not isinstance(job.get("steps"), list):
        return []
    return [step for step in job["steps"] if isinstance(step, dict)]


def workflow_steps(workflow: dict[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
    jobs = workflow.get("jobs")
    if not isinstance(jobs, dict):
        return
    for job_name, job in jobs.items():
        for step in job_steps(job):
            yield str(job_name), step


def find_step(steps: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    return next((step for step in steps if step.get("name") == name), None)


def step_index(steps: list[dict[str, Any]], name: str) -> int | None:
    return next((index for index, step in enumerate(steps) if step.get("name") == name), None)


def parse_platforms(value: Any) -> set[str]:
    return {platform.strip() for platform in str(value or "").split(",") if platform.strip()}


def validate_emulated_builder_order(
    path: Path,
    job_name: str,
    steps: list[dict[str, Any]],
    build_indexes: list[int],
    errors: list[str],
) -> None:
    qemu_indexes = [
        index
        for index, step in enumerate(steps)
        if str(step.get("uses", "")).startswith("docker/setup-qemu-action@")
    ]
    buildx_indexes = [
        index
        for index, step in enumerate(steps)
        if str(step.get("uses", "")).startswith("docker/setup-buildx-action@")
    ]
    require(
        errors,
        len(qemu_indexes) == 1 and len(buildx_indexes) == 1,
        f"{path}: job {job_name!r} must register QEMU once and create exactly one Buildx builder",
    )
    if len(qemu_indexes) != 1 or len(buildx_indexes) != 1:
        return

    buildx_step = steps[buildx_indexes[0]]
    options = (
        buildx_step.get("with")
        if isinstance(buildx_step.get("with"), dict)
        else {}
    )
    config = str(options.get("buildkitd-config-inline", ""))
    require(
        errors,
        re.search(r"\[worker\.oci\]\s+max-parallelism\s*=\s*1", config) is not None,
        f"{path}: job {job_name!r} must serialize its single Buildx builder with max-parallelism = 1",
    )
    require(
        errors,
        bool(build_indexes)
        and qemu_indexes[0] < buildx_indexes[0] < min(build_indexes),
        f"{path}: job {job_name!r} must register QEMU before Buildx and create the builder before every image build",
    )


def environment_name(job: Any) -> str | None:
    if not isinstance(job, dict):
        return None
    environment = job.get("environment")
    if isinstance(environment, str):
        return environment
    if isinstance(environment, dict) and isinstance(environment.get("name"), str):
        return environment["name"]
    return None


def normalized_publish_workflow_sha256(workflow: dict[str, Any]) -> str:
    normalized = copy.deepcopy(workflow)

    def normalize_run_fields(value: Any) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                if key == "run" and isinstance(nested, str):
                    value[key] = nested.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n") + "\n"
                else:
                    normalize_run_fields(nested)
        elif isinstance(value, list):
            for nested in value:
                normalize_run_fields(nested)

    normalize_run_fields(normalized)
    payload = json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_tag_syntax(tag: str) -> bool:
    match = TAG_RE.fullmatch(tag)
    if match is None:
        return False
    prerelease = match.group(4)
    if prerelease is None:
        return True
    return all(not (identifier.isdigit() and len(identifier) > 1 and identifier.startswith("0")) for identifier in prerelease.split("."))


def validate_common_workflow(path: Path, workflow: dict[str, Any], errors: list[str]) -> dict[str, list[tuple[int, dict[str, Any], dict[str, Any]]]]:
    jobs = workflow.get("jobs")
    require(errors, isinstance(jobs, dict), f"{path}: jobs must be a mapping")
    builds: dict[str, list[tuple[int, dict[str, Any], dict[str, Any]]]] = {}
    if not isinstance(jobs, dict):
        return builds

    for job_name, job in jobs.items():
        job_uses = job.get("uses") if isinstance(job, dict) else None
        if isinstance(job_uses, str) and not job_uses.startswith("./"):
            require(
                errors,
                PINNED_ACTION_RE.fullmatch(job_uses) is not None,
                f"{path}: job {job_name!r} has mutable action reference {job_uses!r}; pin it to a full 40-character commit SHA",
            )

        for index, step in enumerate(job_steps(job)):
            uses = step.get("uses")
            if isinstance(uses, str) and not uses.startswith("./"):
                require(
                    errors,
                    PINNED_ACTION_RE.fullmatch(uses) is not None,
                    f"{path}: job {job_name!r} has mutable action reference {uses!r}; pin it to a full 40-character commit SHA",
                )

            if isinstance(uses, str) and uses.startswith("actions/checkout@"):
                options = step.get("with")
                persist = options.get("persist-credentials") if isinstance(options, dict) else None
                require(
                    errors,
                    persist is False,
                    f"{path}: checkout in job {job_name!r} must set persist-credentials: false",
                )

            run = step.get("run")
            if isinstance(run, str):
                require(
                    errors,
                    INPUT_TAG_EXPRESSION_RE.search(run) is None,
                    f"{path}: job {job_name!r} interpolates inputs.tag directly into a run block; pass it through env and validate it first",
                )

            if isinstance(uses, str) and uses.startswith("docker/build-push-action@"):
                options = step.get("with")
                if isinstance(options, dict) and isinstance(options.get("file"), str):
                    builds.setdefault(str(job_name), []).append((index, step, options))

    no_op_steps = [f"{job_name}/{step.get('name')}" for job_name, step in workflow_steps(workflow) if step.get("name") == "Check patch whitespace"]
    require(
        errors,
        not no_op_steps,
        f"{path}: clean-checkout git diff --check steps are no-op gates and must be removed",
    )
    return builds


def validate_build_shapes(
    path: Path,
    records: list[tuple[int, dict[str, Any], dict[str, Any]]],
    errors: list[str],
) -> dict[str, tuple[int, dict[str, Any], dict[str, Any]]]:
    by_file = {str(options.get("file")): (index, step, options) for index, step, options in records if isinstance(options.get("file"), str)}
    require(
        errors,
        set(by_file) == EXPECTED_BUILD_FILES,
        f"{path}: image builds must cover exactly {sorted(EXPECTED_BUILD_FILES)}",
    )
    for dockerfile, (_, _, options) in by_file.items():
        require(
            errors,
            options.get("context") == ".",
            f"{path}: {dockerfile} must use repository root context '.'",
        )
        actual_platforms = parse_platforms(options.get("platforms"))
        require(
            errors,
            actual_platforms == REQUIRED_PLATFORMS,
            f"{path}: {dockerfile} platforms must be exactly {sorted(REQUIRED_PLATFORMS)}; found {sorted(actual_platforms)}",
        )
    return by_file


def validate_ci_workflow(
    path: Path,
    workflow: dict[str, Any],
    builds: dict[str, list[tuple[int, dict[str, Any], dict[str, Any]]]],
    errors: list[str],
) -> None:
    require(
        errors,
        workflow.get("permissions") == {"contents": "read"},
        f"{path}: top-level permissions must be contents: read",
    )
    records = [record for job_records in builds.values() for record in job_records]
    by_file = validate_build_shapes(path, records, errors)
    for dockerfile, (_, _, options) in by_file.items():
        require(
            errors,
            options.get("push") is False,
            f"{path}: CI build for {dockerfile} must set push: false",
        )
    jobs = workflow.get("jobs")
    test_job = jobs.get("test") if isinstance(jobs, dict) else None
    require(
        errors,
        set(builds) == {"test"} and isinstance(test_job, dict),
        f"{path}: all CI image builds must run in the test job",
    )
    if isinstance(test_job, dict):
        validate_emulated_builder_order(
            path,
            "test",
            job_steps(test_job),
            [record[0] for record in builds.get("test", [])],
            errors,
        )


def validate_default_branch_guard(path: Path, job_name: str, job: Any, errors: list[str]) -> None:
    guard = str(job.get("if", "")) if isinstance(job, dict) else ""
    require(
        errors,
        "github.event.repository.default_branch" in guard and "github.ref" in guard and "github.ref_protected" in guard,
        f"{path}: {job_name} job must require dispatch from the protected default branch",
    )


def validate_remote_revalidation(
    path: Path,
    step: dict[str, Any] | None,
    expected_source: str,
    boundary: str,
    errors: list[str],
) -> None:
    if step is None:
        errors.append(f"{path}: missing remote tag revalidation before {boundary}")
        return
    run = str(step.get("run", ""))
    env = step.get("env") if isinstance(step.get("env"), dict) else {}
    branch_tokens = (
        "get_repository_state()",
        'repository_state="$(get_repository_state)"',
        'repository_state_after="$(get_repository_state)"',
        '[[ "${repository_state_after}" != "${repository_state}" ]]',
        "gh api",
        '"repos/${GITHUB_REPOSITORY}"',
        "--jq '[.full_name, .default_branch] | @tsv'",
        '"${live_repository,,}" != "${GITHUB_REPOSITORY,,}"',
        '"${live_default_branch}" != "${VALIDATED_DEFAULT_BRANCH}"',
        'git check-ref-format "refs/heads/${live_default_branch}"',
        'branch_ref="refs/heads/${live_default_branch}"',
        'branch_refs="$(git ls-remote --exit-code "${REMOTE_URL}" "${branch_ref}")"',
        "branch_count=0",
        "branch_unexpected=false",
        '[[ "${branch_count}" -ne 1 || "${branch_unexpected}" == "true" || "${branch_sha}" != "${SOURCE_SHA}" ]]',
    )
    require(
        errors,
        all(token in run for token in branch_tokens)
        and env.get("GH_TOKEN") == "${{ github.token }}"
        and env.get("VALIDATED_DEFAULT_BRANCH")
        == "${{ github.event.repository.default_branch }}"
        and "DEFAULT_BRANCH" not in env,
        f"{path}: remote revalidation before {boundary} must query the live repository identity and require its current protected default branch to resolve exactly once to the validated source SHA",
    )
    lookup_tokens = (
        "git ls-remote",
        'direct_ref="refs/tags/${RELEASE_TAG}"',
        "refs/tags/${RELEASE_TAG}^{}",
    )
    require(
        errors,
        all(token in run for token in lookup_tokens)
        and env.get("SOURCE_SHA") == expected_source
        and "RELEASE_TAG" in env,
        f"{path}: remote tag revalidation before {boundary} must query the direct and peeled protected tag refs",
    )
    exact_ref_tokens = (
        "direct_count=0",
        "peeled_count=0",
        "unexpected_ref=false",
        '[[ "${direct_count}" -ne 1 || "${peeled_count}" -ne 1 || "${unexpected_ref}" == "true" ]]',
    )
    require(
        errors,
        all(token in run for token in exact_ref_tokens)
        and "else print direct_sha" not in run
        and "remote_commit" not in run,
        f"{path}: remote tag revalidation before {boundary} must require exactly one direct tag-object ref and one peeled ref with no fallback",
    )
    require(
        errors,
        '[[ "${direct_sha}" == "${peeled_sha}" ]]' in run,
        f"{path}: remote tag revalidation before {boundary} must require a distinct tag-object SHA",
    )
    require(
        errors,
        '[[ "${peeled_sha}" != "${SOURCE_SHA}" ]]' in run,
        f"{path}: remote tag revalidation before {boundary} peeled SHA must equal the validated source SHA",
    )


def validate_publish_builds(
    path: Path,
    publish_steps: list[dict[str, Any]],
    records: list[tuple[int, dict[str, Any], dict[str, Any]]],
    errors: list[str],
) -> dict[str, tuple[int, dict[str, Any], dict[str, Any]]]:
    by_file = validate_build_shapes(path, records, errors)
    for dockerfile, (_, step, options) in by_file.items():
        require(
            errors,
            options.get("push") is True,
            f"{path}: publish build for {dockerfile} must set push: true",
        )
        require(
            errors,
            options.get("sbom") is True,
            f"{path}: publish build for {dockerfile} must set sbom: true",
        )
        require(
            errors,
            options.get("provenance") == "mode=max",
            f"{path}: publish build for {dockerfile} must set provenance: mode=max",
        )
        require(
            errors,
            step.get("id") == EXPECTED_BUILD_IDS.get(dockerfile),
            f"{path}: publish build for {dockerfile} must expose its canonical build ID",
        )
        tags = str(options.get("tags", ""))
        require(
            errors,
            "candidate-" in tags and "needs.validate.outputs.source_sha" in tags,
            f"{path}: publish build {step.get('name', '<unnamed>')!r} must stage a source-SHA candidate tag instead of a final release tag",
        )
        require(
            errors,
            "outputs.tag" not in tags and "outputs.version" not in tags,
            f"{path}: publish build {step.get('name', '<unnamed>')!r} must not push final release/version tags before digest verification",
        )
    return by_file


def validate_publish_workflow(
    path: Path,
    workflow: dict[str, Any],
    builds: dict[str, list[tuple[int, dict[str, Any], dict[str, Any]]]],
    errors: list[str],
) -> None:
    require(
        errors,
        normalized_publish_workflow_sha256(workflow) == CANONICAL_PUBLISH_WORKFLOW_SHA256,
        f"{path}: publish workflow must match its canonical publish workflow hash; all parsed workflow metadata and executable content are integrity-bound",
    )
    trigger = workflow_trigger(workflow)
    require(
        errors,
        isinstance(trigger, dict) and set(trigger) == {"workflow_dispatch"},
        f"{path}: release publication must be workflow_dispatch-only; push/tag triggers are forbidden",
    )
    if isinstance(trigger, dict):
        dispatch = trigger.get("workflow_dispatch")
        inputs = dispatch.get("inputs") if isinstance(dispatch, dict) else None
        tag_input = inputs.get("tag") if isinstance(inputs, dict) else None
        require(
            errors,
            isinstance(tag_input, dict) and tag_input.get("required") is True and tag_input.get("type") == "string",
            f"{path}: workflow_dispatch must require a string tag input",
        )
        tag_description = (
            str(tag_input.get("description", ""))
            if isinstance(tag_input, dict)
            else ""
        )
        require(
            errors,
            "protected release tag" in tag_description.lower()
            and re.search(r"v?\d+\.\d+\.\d+", tag_description) is None,
            f"{path}: workflow_dispatch tag description must be version-neutral and identify a protected release tag",
        )

    require(
        errors,
        workflow.get("permissions") == {},
        f"{path}: top-level permissions must be empty",
    )
    concurrency = workflow.get("concurrency")
    require(
        errors,
        isinstance(concurrency, dict)
        and concurrency.get("group") == "publish-ghcr-release"
        and concurrency.get("cancel-in-progress") is False,
        f"{path}: concurrency must serialize all release publications with cancel-in-progress: false",
    )

    jobs = workflow.get("jobs")
    if not isinstance(jobs, dict):
        return
    require(
        errors,
        set(jobs) == {"validate", "gate", "publish", "prerelease"},
        f"{path}: release workflow jobs must be exactly validate, gate, publish, and prerelease",
    )
    validate_job = jobs.get("validate")
    gate_job = jobs.get("gate")
    publish_job = jobs.get("publish")
    prerelease_job = jobs.get("prerelease")
    require(
        errors,
        isinstance(validate_job, dict),
        f"{path}: missing read-only validate job",
    )
    require(errors, isinstance(gate_job, dict), f"{path}: missing release-control gate job")
    require(errors, isinstance(publish_job, dict), f"{path}: missing package publish job")
    require(errors, isinstance(prerelease_job, dict), f"{path}: missing prerelease job")
    if not all(
        isinstance(job, dict)
        for job in (validate_job, gate_job, publish_job, prerelease_job)
    ):
        return

    require(
        errors,
        validate_job.get("permissions") == {"contents": "read"},
        f"{path}: validate job permissions must be exactly contents: read",
    )
    require(
        errors,
        gate_job.get("permissions") == {"actions": "read", "contents": "read"},
        f"{path}: release-control gate permissions must be exactly actions: read and contents: read",
    )
    require(
        errors,
        publish_job.get("permissions") == {"contents": "read", "packages": "write"},
        f"{path}: publish job permissions must be exactly contents: read and packages: write",
    )
    require(
        errors,
        prerelease_job.get("permissions") == {"contents": "write"},
        f"{path}: prerelease job permissions must be exactly contents: write",
    )
    require(
        errors,
        gate_job.get("needs") == "validate",
        f"{path}: release-control gate must depend on validate",
    )
    require(
        errors,
        publish_job.get("needs") == ["validate", "gate"],
        f"{path}: publish job must depend on validate and the release-control gate",
    )
    require(
        errors,
        prerelease_job.get("needs") == "publish",
        f"{path}: prerelease job must depend on publish",
    )
    require(
        errors,
        environment_name(validate_job) is None
        and environment_name(gate_job) is None
        and environment_name(publish_job) == "release"
        and environment_name(prerelease_job) == "release",
        f"{path}: only publish and prerelease jobs may use the protected release environment",
    )
    for job_name, job in (
        ("validate", validate_job),
        ("gate", gate_job),
        ("publish", publish_job),
        ("prerelease", prerelease_job),
    ):
        validate_default_branch_guard(path, job_name, job, errors)

    expected_validate_outputs = {
        "tag": "${{ steps.release_input.outputs.tag }}",
        "version": "${{ steps.release_input.outputs.version }}",
        "is_prerelease": "${{ steps.release_input.outputs.is_prerelease }}",
        "source_sha": "${{ steps.source.outputs.sha }}",
    }
    require(
        errors,
        validate_job.get("outputs") == expected_validate_outputs,
        f"{path}: validate job must export validated prerelease mode with the tag, version, and source SHA",
    )
    expected_publish_outputs = {
        "tag": "${{ needs.validate.outputs.tag }}",
        "version": "${{ needs.validate.outputs.version }}",
        "is_prerelease": "${{ needs.validate.outputs.is_prerelease }}",
        "source_sha": "${{ needs.validate.outputs.source_sha }}",
        "runtime_digest": "${{ steps.runtime_build.outputs.digest }}",
        "addon_digest": "${{ steps.addon_build.outputs.digest }}",
    }
    require(
        errors,
        publish_job.get("outputs") == expected_publish_outputs,
        f"{path}: publish job must pass validated release outputs and verified runtime and add-on digest outputs to prerelease",
    )

    validate_steps = job_steps(validate_job)
    publish_steps = job_steps(publish_job)
    prerelease_steps = job_steps(prerelease_job)
    release_input_step = find_step(validate_steps, "Validate requested release tag")
    release_input_run = (
        str(release_input_step.get("run", "")) if release_input_step else ""
    )
    release_mode_tokens = (
        "is_prerelease=false",
        'if [[ "${REQUESTED_TAG}" == *-* ]]',
        "is_prerelease=true",
        "printf 'is_prerelease=%s\\n' \"${is_prerelease}\"",
    )
    require(
        errors,
        release_input_step is not None
        and release_input_step.get("id") == "release_input"
        and all(token in release_input_run for token in release_mode_tokens),
        f"{path}: validated prerelease mode must be derived from the validated tag and exported by the release-input step",
    )
    dispatch_step = find_step(validate_steps, "Preserve protected workflow dispatch SHA")
    source_step = find_step(validate_steps, "Verify checked-out tag and source commit")
    validate_checkout_indexes = [
        index
        for index, step in enumerate(validate_steps)
        if str(step.get("uses", "")).startswith("actions/checkout@")
    ]
    dispatch_index = step_index(
        validate_steps, "Preserve protected workflow dispatch SHA"
    )
    source_index = step_index(
        validate_steps, "Verify checked-out tag and source commit"
    )
    dispatch_env = (
        dispatch_step.get("env")
        if dispatch_step and isinstance(dispatch_step.get("env"), dict)
        else {}
    )
    dispatch_run = str(dispatch_step.get("run", "")) if dispatch_step else ""
    require(
        errors,
        dispatch_step is not None
        and dispatch_step.get("id") == "dispatch_source"
        and dispatch_env == {"DISPATCH_SHA": "${{ github.sha }}"}
        and '[[ ! "${DISPATCH_SHA}" =~ ^[0-9a-f]{40}$ ]]' in dispatch_run
        and "printf 'sha=%s\\n' \"${DISPATCH_SHA}\"" in dispatch_run
        and len(validate_checkout_indexes) == 1
        and dispatch_index is not None
        and source_index is not None
        and dispatch_index < validate_checkout_indexes[0] < source_index,
        f"{path}: validate job must preserve trusted github.sha before and through tag checkout",
    )
    source_env = (
        source_step.get("env")
        if source_step and isinstance(source_step.get("env"), dict)
        else {}
    )
    source_run = str(source_step.get("run", "")) if source_step else ""
    require(
        errors,
        source_step is not None
        and source_step.get("id") == "source"
        and source_env.get("DISPATCH_SHA")
        == "${{ steps.dispatch_source.outputs.sha }}"
        and '[[ "${tag_sha}" != "${DISPATCH_SHA}" ]]' in source_run
        and "merge-base --is-ancestor" not in source_run
        and "printf 'sha=%s\\n' \"${DISPATCH_SHA}\"" in source_run
        and "printf 'sha=%s\\n' \"${head_sha}\"" not in source_run
        and "printf 'sha=%s\\n' \"${tag_sha}\"" not in source_run,
        f"{path}: source verification must enforce dispatch SHA exact equality and export only that trusted SHA",
    )
    require(
        errors,
        'git cat-file -t "refs/tags/${RELEASE_TAG}"' in source_run
        and '[[ "${tag_type}" != "tag" ]]' in source_run,
        f"{path}: checked-out release ref must be verified as an annotated tag object",
    )

    gate_steps = job_steps(gate_job)
    require(
        errors,
        tuple(str(step.get("name", "")) for step in gate_steps)
        == (
            "Check out validated source SHA for release gate",
            "Set up Python",
            "Verify public release controls",
        ),
        f"{path}: release-control gate must use only the reviewed checkout, Python setup, and verification steps",
    )
    gate_checkouts = [
        step
        for step in gate_steps
        if str(step.get("uses", "")).startswith("actions/checkout@")
    ]
    gate_checkout = gate_checkouts[0] if len(gate_checkouts) == 1 else None
    gate_checkout_options = (
        gate_checkout.get("with") if isinstance(gate_checkout, dict) else None
    )
    require(
        errors,
        isinstance(gate_checkout_options, dict)
        and gate_checkout_options.get("ref")
        == "${{ needs.validate.outputs.source_sha }}"
        and gate_checkout_options.get("persist-credentials") is False,
        f"{path}: release-control gate checkout must use the exact validated source SHA with no credentials",
    )
    gate_setup = find_step(gate_steps, "Set up Python")
    require(
        errors,
        gate_setup is not None
        and str(gate_setup.get("uses", "")).startswith("actions/setup-python@")
        and gate_setup.get("with") == {"python-version": "3.11"},
        f"{path}: release-control gate must use pinned Python 3.11 without dependency caches",
    )
    gate_step = find_step(gate_steps, "Verify public release controls")
    gate_run = str(gate_step.get("run", "")) if gate_step else ""
    gate_env = (
        gate_step.get("env")
        if gate_step and isinstance(gate_step.get("env"), dict)
        else {}
    )
    expected_gate_env = {
        "GH_TOKEN": "${{ github.token }}",
        "DEFAULT_BRANCH": "${{ github.event.repository.default_branch }}",
        "RELEASE_TAG": "${{ needs.validate.outputs.tag }}",
        "SOURCE_SHA": "${{ needs.validate.outputs.source_sha }}",
    }
    gate_tokens = (
        "set -euo pipefail",
        'api_version="2026-03-10"',
        EXPECTED_RELEASE_CONTROL_TOOL_SHA256,
        "tools/verify_release_controls.py | sha256sum --check --strict",
        "actions/workflows/ci.yml/runs",
        "rules/branches/${encoded_branch}",
        "environments/release",
        '"repos/${GITHUB_REPOSITORY}/rulesets"',
        '"repos/${GITHUB_REPOSITORY}/rulesets/${ruleset_id}"',
        "verify_release_controls.py list-ruleset-ids",
        "verify_release_controls.py verify",
        '--repository "${GITHUB_REPOSITORY}"',
        '--default-branch "${DEFAULT_BRANCH}"',
        '--release-tag "${RELEASE_TAG}"',
        '--source-sha "${SOURCE_SHA}"',
    )
    require(
        errors,
        gate_step is not None
        and gate_step.get("shell") == "bash"
        and gate_env == expected_gate_env
        and all(token in gate_run for token in gate_tokens)
        and "<<'PY'" not in gate_run
        and gate_step.get("continue-on-error") is not True
        and "if" not in gate_step,
        f"{path}: release-control gate must hash-check the reviewed verifier, capture every required GitHub control through the pinned API, and fail closed at the exact validated SHA",
    )

    require(
        errors,
        tuple(str(step.get("name", "")) for step in publish_steps) == PUBLISH_STEP_SEQUENCE,
        f"{path}: package-write job must use only the reviewed publish step sequence",
    )
    for step_name, action_prefix in PUBLISH_ACTION_PREFIXES.items():
        action_step = find_step(publish_steps, step_name)
        require(
            errors,
            action_step is not None and str(action_step.get("uses", "")).startswith(action_prefix),
            f"{path}: reviewed publish step {step_name!r} must use {action_prefix.rstrip('@')}",
        )
    require(
        errors,
        tuple(str(step.get("name", "")) for step in prerelease_steps)
        == (
            "Revalidate remote tag before release",
            "Verify or create idempotent GitHub release",
        ),
        f"{path}: prerelease job must use only remote revalidation and release creation steps",
    )
    validate_run = "\n".join(str(step.get("run", "")) for step in validate_steps)
    require(
        errors,
        "pytest" in validate_run and "validate_release.py" in validate_run and "validate_ha_addon_repo.py" in validate_run and "compileall" in validate_run,
        f"{path}: validate job must run release/add-on validation, compile checks, and tests",
    )
    privileged_run = "\n".join(str(step.get("run", "")) for step in publish_steps).lower()
    require(
        errors,
        all(token not in privileged_run for token in ("pip install", "pytest", "validate_release.py")),
        f"{path}: package-write job must not execute tag-controlled validation or test tooling",
    )

    publish_checkouts = [step for step in publish_steps if str(step.get("uses", "")).startswith("actions/checkout@")]
    exact_checkout = publish_checkouts[0] if len(publish_checkouts) == 1 else None
    options = exact_checkout.get("with") if isinstance(exact_checkout, dict) else None
    require(
        errors,
        isinstance(options, dict) and options.get("ref") == "${{ needs.validate.outputs.source_sha }}" and options.get("persist-credentials") is False,
        f"{path}: package job checkout must use the exact validated source SHA with no credentials",
    )

    by_file = validate_publish_builds(path, publish_steps, builds.get("publish", []), errors)
    build_indexes = [record[0] for record in by_file.values()]
    validate_emulated_builder_order(
        path,
        "publish",
        publish_steps,
        [record[0] for record in builds.get("publish", [])],
        errors,
    )
    verify_name = "Verify candidate manifests by digest"
    candidate_gate_name = "Validate and scan every staged candidate platform"
    guard_name = "Guard final tags against conflicting digests"
    revalidate_promotion_name = "Revalidate remote tag before final promotion"
    promotion_name = "Promote verified digests to release tags"
    post_name = "Verify promoted release tags"
    verify_step = find_step(publish_steps, verify_name)
    candidate_gate_step = find_step(publish_steps, candidate_gate_name)
    guard_step = find_step(publish_steps, guard_name)
    promotion_step = find_step(publish_steps, promotion_name)
    post_step = find_step(publish_steps, post_name)
    verify_index = step_index(publish_steps, verify_name)
    candidate_gate_index = step_index(publish_steps, candidate_gate_name)
    guard_index = step_index(publish_steps, guard_name)
    revalidate_promotion_index = step_index(publish_steps, revalidate_promotion_name)
    promotion_index = step_index(publish_steps, promotion_name)
    post_index = step_index(publish_steps, post_name)

    verify_run = str(verify_step.get("run", "")) if verify_step else ""
    verify_env = verify_step.get("env") if verify_step and isinstance(verify_step.get("env"), dict) else {}
    required_verify_tokens = {
        "docker buildx imagetools inspect",
        "${RUNTIME_IMAGE}@${RUNTIME_DIGEST}",
        "${ADDON_IMAGE}@${ADDON_DIGEST}",
        *REQUIRED_PLATFORMS,
    }
    require(
        errors,
        verify_step is not None
        and all(token in verify_run for token in required_verify_tokens)
        and verify_env.get("RUNTIME_DIGEST") == "${{ steps.runtime_build.outputs.digest }}"
        and verify_env.get("ADDON_DIGEST") == "${{ steps.addon_build.outputs.digest }}",
        f"{path}: digest verification must inspect both build output digests on all four platforms",
    )
    require(
        errors,
        verify_index is not None and build_indexes and verify_index > max(build_indexes),
        f"{path}: digest verification must run after both publish builds",
    )

    candidate_gate_run = (
        str(candidate_gate_step.get("run", "")) if candidate_gate_step else ""
    )
    candidate_gate_env = (
        candidate_gate_step.get("env")
        if candidate_gate_step and isinstance(candidate_gate_step.get("env"), dict)
        else {}
    )
    candidate_gate_tokens = {
        'manifest_ref="${image}@${manifest_digest}"',
        'platform_ref="${image}@${platform_digest}"',
        'docker buildx imagetools inspect "${manifest_ref}" --raw',
        "jq -er",
        "^sha256:[0-9a-f]{64}$",
        'docker pull --platform "${platform}" "${platform_ref}"',
        'docker run --rm --pull never --network none --platform "${platform}"',
        "--read-only",
        "--tmpfs /tmp",
        "--cap-drop ALL",
        "--security-opt no-new-privileges:true",
        "--user 10001:10001",
        "--entrypoint python",
        "/usr/local/bin/validate_container_artifact.py",
        PINNED_TRIVY_IMAGE,
        "--image-src docker",
        '--platform "${platform}"',
        "--scanners vuln,secret",
        "--severity HIGH,CRITICAL",
        "--exit-code 1",
        'validate_and_scan "${RUNTIME_IMAGE}" "${RUNTIME_DIGEST}"',
        'validate_and_scan "${ADDON_IMAGE}" "${ADDON_DIGEST}"',
        *REQUIRED_PLATFORMS,
    }
    require(
        errors,
        candidate_gate_step is not None
        and all(token in candidate_gate_run for token in candidate_gate_tokens)
        and "candidate-" not in candidate_gate_run
        and candidate_gate_env
        == {
            "RUNTIME_DIGEST": "${{ steps.runtime_build.outputs.digest }}",
            "ADDON_DIGEST": "${{ steps.addon_build.outputs.digest }}",
        }
        and candidate_gate_step.get("continue-on-error") is not True
        and "if" not in candidate_gate_step,
        f"{path}: candidate platform validation must fail closed, resolve immutable child digests for all four platforms, run the hardened artifact validator, and scan the same bytes with pinned Trivy",
    )
    require(
        errors,
        verify_index is not None
        and candidate_gate_index is not None
        and guard_index is not None
        and verify_index < candidate_gate_index < guard_index,
        f"{path}: candidate platform validation must run after manifest verification and before the conflict guard",
    )

    guard_run = str(guard_step.get("run", "")) if guard_step else ""
    guard_tokens = (
        "docker buildx imagetools inspect",
        "--format '{{json .Manifest}}'",
        "jq -er '.digest'",
        "^sha256:[0-9a-f]{64}$",
        'error_text="$(cat "${error_file}")"',
        '[[ -z "${actual_digest}" && "${error_text}" == "ERROR: ${ref}: not found" ]]',
        "unable to determine whether ${ref} already exists",
        "already resolves to expected digest",
        "ref exists with conflicting digest",
        "${RUNTIME_IMAGE}:${RELEASE_TAG}",
        "${RUNTIME_IMAGE}:${RELEASE_VERSION}",
        "${ADDON_IMAGE}:${RELEASE_TAG}",
        "${ADDON_IMAGE}:${RELEASE_VERSION}",
    )
    require(
        errors,
        guard_step is not None and all(token in guard_run for token in guard_tokens),
        f"{path}: conflict guard must use verified JSON manifest digest extraction, inspect "
        "all four final refs, allow absent/equal refs, and reject conflicting digests",
    )

    promotion_run = str(promotion_step.get("run", "")) if promotion_step else ""
    promotion_tokens = (
        "docker buildx imagetools create",
        "${RUNTIME_IMAGE}@${RUNTIME_DIGEST}",
        "${ADDON_IMAGE}@${ADDON_DIGEST}",
        "${RUNTIME_IMAGE}:${RELEASE_TAG}",
        "${RUNTIME_IMAGE}:${RELEASE_VERSION}",
        "${ADDON_IMAGE}:${RELEASE_TAG}",
        "${ADDON_IMAGE}:${RELEASE_VERSION}",
    )
    require(
        errors,
        promotion_step is not None and all(token in promotion_run for token in promotion_tokens),
        f"{path}: digest-bound promotion must create all final refs from the verified digests",
    )

    post_run = str(post_step.get("run", "")) if post_step else ""
    post_tokens = (
        "docker buildx imagetools inspect",
        "--format '{{json .Manifest}}'",
        "jq -er '.digest'",
        "^sha256:[0-9a-f]{64}$",
        "post-promotion ref does not resolve to expected digest",
        "${RUNTIME_IMAGE}:${RELEASE_TAG}",
        "${RUNTIME_IMAGE}:${RELEASE_VERSION}",
        "${ADDON_IMAGE}:${RELEASE_TAG}",
        "${ADDON_IMAGE}:${RELEASE_VERSION}",
    )
    require(
        errors,
        post_step is not None and all(token in post_run for token in post_tokens),
        f"{path}: post-promotion verification must use verified JSON manifest digest extraction and bind all four final refs to build digests",
    )
    require(
        errors,
        None
        not in (
            verify_index,
            candidate_gate_index,
            guard_index,
            revalidate_promotion_index,
            promotion_index,
            post_index,
        )
        and verify_index
        < candidate_gate_index
        < guard_index
        < revalidate_promotion_index
        < promotion_index
        < post_index,
        f"{path}: candidate validation, conflict guard, remote revalidation, promotion, and post-promotion verification must run in the safe order",
    )

    login_index = next(
        (index for index, step in enumerate(publish_steps) if str(step.get("uses", "")).startswith("docker/login-action@")),
        None,
    )
    login_revalidation = find_step(publish_steps, "Revalidate remote tag before package login")
    promotion_revalidation = find_step(publish_steps, revalidate_promotion_name)
    validate_remote_revalidation(
        path,
        login_revalidation,
        "${{ needs.validate.outputs.source_sha }}",
        "package login",
        errors,
    )
    validate_remote_revalidation(
        path,
        promotion_revalidation,
        "${{ needs.validate.outputs.source_sha }}",
        "final promotion",
        errors,
    )
    require(
        errors,
        login_index is not None and login_index > 0 and publish_steps[login_index - 1].get("name") == "Revalidate remote tag before package login",
        f"{path}: remote tag revalidation must run immediately before package login",
    )
    require(
        errors,
        promotion_index is not None and promotion_index > 0 and publish_steps[promotion_index - 1].get("name") == revalidate_promotion_name,
        f"{path}: remote tag revalidation must run immediately before final promotion",
    )

    prerelease_revalidation = find_step(prerelease_steps, "Revalidate remote tag before release")
    validate_remote_revalidation(
        path,
        prerelease_revalidation,
        "${{ needs.publish.outputs.source_sha }}",
        "prerelease",
        errors,
    )
    create_step = find_step(prerelease_steps, "Verify or create idempotent GitHub release")
    create_run = str(create_step.get("run", "")) if create_step else ""
    create_env = create_step.get("env") if create_step and isinstance(create_step.get("env"), dict) else {}
    release_tokens = (
        "gh release create",
        '--target "${SOURCE_SHA}"',
        "--verify-tag",
        '"${release_state_args[@]}"',
    )
    release_channel_block = """case "${EXPECTED_PRERELEASE}" in
  true)
    expected_prerelease=true
    release_state_args=(--prerelease --latest=false)
    ;;
  false)
    expected_prerelease=false
    release_state_args=(--latest)
    ;;
  *)
    echo "ERROR: validated prerelease state must be exactly true or false" >&2
    exit 1
    ;;
esac"""
    require(
        errors,
        create_step is not None
        and all(token in create_run for token in release_tokens)
        and create_env.get("SOURCE_SHA") == "${{ needs.publish.outputs.source_sha }}",
        f"{path}: GitHub release must target the validated source SHA with --verify-tag and the validated release-channel arguments",
    )
    require(
        errors,
        release_channel_block in create_run,
        f"{path}: GitHub release must derive stable and prerelease channels from trusted validated state, mark stable releases Latest, and force prereleases non-Latest",
    )
    require(
        errors,
        create_env.get("EXPECTED_PRERELEASE")
        == "${{ needs.publish.outputs.is_prerelease }}"
        and 'if [[ "${RELEASE_TAG}" == *-* ]]' not in create_run,
        f"{path}: GitHub release must consume only the trusted validated prerelease mode without re-deriving it from the tag",
    )
    expected_prerelease_digest_env = {
        "RUNTIME_IMAGE": "${{ env.RUNTIME_IMAGE }}",
        "ADDON_IMAGE": "${{ env.ADDON_IMAGE }}",
        "RUNTIME_DIGEST": "${{ needs.publish.outputs.runtime_digest }}",
        "ADDON_DIGEST": "${{ needs.publish.outputs.addon_digest }}",
    }
    require(
        errors,
        all(
            create_env.get(name) == value
            for name, value in expected_prerelease_digest_env.items()
        ),
        f"{path}: prerelease digest inputs must come from the verified publish outputs and canonical image names",
    )
    digest_pattern = "digest_pattern='^sha256:[0-9a-f]{64}$'"

    def has_fail_closed_digest_gate(name: str) -> bool:
        return (
            re.search(
                rf'if \[\[ ! "\$\{{{name}\}}" =~ \$\{{digest_pattern\}} \]\]; then.*?exit 1\s+fi',
                create_run,
                re.DOTALL,
            )
            is not None
        )

    require(
        errors,
        digest_pattern in create_run
        and has_fail_closed_digest_gate("RUNTIME_DIGEST")
        and has_fail_closed_digest_gate("ADDON_DIGEST"),
        f"{path}: prerelease must fail closed on invalid digest syntax before creating release notes",
    )
    require(
        errors,
        "${RUNTIME_IMAGE}@${RUNTIME_DIGEST}" in create_run
        and "${ADDON_IMAGE}@${ADDON_DIGEST}" in create_run,
        f"{path}: prerelease notes must record both immutable image refs",
    )
    require(
        errors,
        all(platform in create_run for platform in REQUIRED_PLATFORMS),
        f"{path}: prerelease notes must record every supported platform",
    )
    require(
        errors,
        'release_notes_path="docs/releases/${RELEASE_TAG}.md"' in create_run,
        f"{path}: prerelease must use the canonical curated release notes path docs/releases/${{RELEASE_TAG}}.md",
    )
    curated_fetch_tokens = (
        "if ! gh api --method GET",
        "application/vnd.github.raw+json",
        '"repos/${GITHUB_REPOSITORY}/contents/${release_notes_path}"',
        '-f ref="${SOURCE_SHA}"',
        '> "${curated_notes_file}"',
        "unable to fetch curated release notes",
    )
    curated_fetch_gate = re.search(
        r"if ! gh api --method GET.*?unable to fetch curated release notes.*?exit 1\s+fi",
        create_run,
        re.DOTALL,
    )
    require(
        errors,
        all(token in create_run for token in curated_fetch_tokens)
        and curated_fetch_gate is not None,
        f"{path}: prerelease must fetch curated release notes through the GitHub Contents API at the verified source SHA and fail closed when the curated release notes fetch fails",
    )
    curated_blank_gate = re.search(
        r'if \[\[ ! -s "\$\{curated_notes_file\}" \]\].*?'
        r"grep -q '\[\^\[:space:\]\]' \"\$\{curated_notes_file\}\"; then.*?"
        r"curated release notes are missing or blank.*?exit 1\s+fi",
        create_run,
        re.DOTALL,
    )
    require(
        errors,
        curated_blank_gate is not None,
        f"{path}: prerelease must fail closed when curated release notes are missing or blank",
    )
    require(
        errors,
        'printf \'%s\\n\\n\' "${expected_notes_prefix}"' in create_run
        and 'cat "${curated_notes_file}"' in create_run
        and '} > "${release_body_file}"' in create_run,
        f"{path}: prerelease must combine immutable image metadata and curated release notes in one release body file",
    )
    require(
        errors,
        '--notes-file "${release_body_file}"' in create_run,
        f"{path}: prerelease must publish the exact composed body with --notes-file",
    )
    require(
        errors,
        "--generate-notes" not in create_run and '--notes "' not in create_run,
        f"{path}: prerelease must not use generated release notes or an inline notes fragment",
    )
    existing_guard_tokens = (
        "lookup_release() {",
        'gh api "repos/${GITHUB_REPOSITORY}/releases/tags/${RELEASE_TAG}"',
        'readonly LOOKUP_ABSENT=4',
        'readonly LOOKUP_FAILED=10',
        'readonly LOOKUP_AMBIGUOUS=11',
        'release_was_absent=false',
        'release_was_absent=true',
        'verify_release "existing release" "${existing_release}"',
        'if [[ "${release_was_absent}" == "true" ]]; then',
        "Only an explicit, successfully queried absence permits creation",
    )
    require(
        errors,
        all(token in create_run for token in existing_guard_tokens),
        f"{path}: prerelease must use an exact existing-release guard and create only after explicit absence",
    )
    require(
        errors,
        '--arg tag "${RELEASE_TAG}"' in create_run
        and create_run.count(".tag_name == $tag") >= 2
        and '--arg sha "${SOURCE_SHA}"' not in create_run
        and "target_commitish" not in create_run,
        f"{path}: existing prerelease verification must require the matching release tag without relying on target_commitish",
    )
    require(
        errors,
        '--argjson expected_prerelease "${expected_prerelease}"' in create_run
        and ".draft == false and .prerelease == $expected_prerelease" in create_run,
        f"{path}: existing release verification must require the exact non-draft stable or prerelease state",
    )
    release_schema_tokens = (
        "validate_release_schema() {",
        'has("id")',
        '.id | type == "number" and . > 0',
        'has("immutable")',
        '.immutable | type == "boolean"',
    )
    require(
        errors,
        all(token in create_run for token in release_schema_tokens),
        f"{path}: release lookups must require a typed release ID and immutable state",
    )
    immutable_release_block = re.search(
        r"\(\.draft == false and \.prerelease == \$expected_prerelease\) and\s+"
        r"\(\.immutable == true\) and\s+"
        r'\(\(\.body // ""\) == \$expected_body\)',
        create_run,
    )
    require(
        errors,
        immutable_release_block is not None,
        f"{path}: exact release verification must require an immutable published release",
    )
    require(
        errors,
        '--rawfile expected_body "${release_body_file}"' in create_run
        and '(.body // "") == $expected_body' in create_run
        and "startswith($prefix)" not in create_run,
        f"{path}: existing release verification must require the exact full release body",
    )
    lookup_failure_tokens = (
        "\\(HTTP 404\\)[[:space:]]*$",
        'return "${LOOKUP_ABSENT}"',
        'return "${LOOKUP_FAILED}"',
        'return "${LOOKUP_AMBIGUOUS}"',
        "release lookup failed without an explicit HTTP 404",
        "refusing release creation after lookup status",
    )
    require(
        errors,
        all(token in create_run for token in lookup_failure_tokens)
        and create_run.count('return "${LOOKUP_ABSENT}"') == 2
        and create_run.count('return "${LOOKUP_FAILED}"') == 2
        and create_run.count('return "${LOOKUP_AMBIGUOUS}"') == 2,
        f"{path}: prerelease must distinguish explicit absence from lookup failure or ambiguity",
    )
    require(
        errors,
        "verify_final_release() {" in create_run
        and 'verify_final_release "${create_status}"' in create_run
        and 'verify_release "post-create release" "${final_release}"' in create_run,
        f"{path}: prerelease must post-verify the final prerelease after every create or retry path",
    )
    require(
        errors,
        'verified_release_id="$(jq -er' in create_run
        and '.id | select(type == "number" and . > 0)' in create_run,
        f"{path}: post-create verification must extract the typed positive verified release ID",
    )
    retry_tokens = (
        "create_status=$?",
        "Release creation returned status",
        "An exact release already exists; accepting the idempotent retry",
        "An exact release now exists; accepting the idempotent retry after a lost response or race",
        'local create_status="$1"',
    )
    require(
        errors,
        all(token in create_run for token in retry_tokens),
        f"{path}: prerelease must preserve idempotent retry and race recovery while failing on mismatches",
    )
    latest_tokens = (
        "verify_latest_release() {",
        "lookup_latest_release() {",
        'if [[ "${expected_prerelease}" == "true" ]]',
        'gh api "repos/${GITHUB_REPOSITORY}/releases/latest"',
        ".draft == false and .prerelease == false",
        "stable release is not the repository Latest release",
        'verify_final_release "${create_status}"\nverify_latest_release',
    )
    require(
        errors,
        all(token in create_run for token in latest_tokens),
        f"{path}: GitHub release must verify stable releases as Latest after exact post-create verification",
    )
    stable_latest_block = re.search(
        r"\(\.id == \$expected_id\) and\s+"
        r"\(\.tag_name == \$tag\) and\s+"
        r"\(\.draft == false and \.prerelease == false\) and\s+"
        r"\(\.immutable == true\)",
        create_run,
    )
    require(
        errors,
        stable_latest_block is not None,
        f"{path}: stable Latest verification must require the exact stable release ID as Latest",
    )
    prerelease_latest_block = """--argjson expected_id "${verified_release_id}" \\
      '(.id != $expected_id) and
       (.draft == false and .prerelease == false)'"""
    require(
        errors,
        prerelease_latest_block in create_run
        and "latest release unexpectedly resolves to the prerelease" in create_run
        and '[[ "${latest_status}" -eq "${LOOKUP_ABSENT}" ]]' in create_run,
        f"{path}: Latest verification must prove prereleases are not Latest while accepting only an explicit missing Latest release",
    )
    create_index = step_index(prerelease_steps, "Verify or create idempotent GitHub release")
    require(
        errors,
        create_index is not None and create_index > 0 and prerelease_steps[create_index - 1].get("name") == "Revalidate remote tag before release",
        f"{path}: remote tag revalidation must run immediately before prerelease creation",
    )


def validate_workflow(path: Path, workflow: dict[str, Any], errors: list[str]) -> None:
    builds = validate_common_workflow(path, workflow, errors)
    if path == CI_WORKFLOW:
        validate_ci_workflow(path, workflow, builds, errors)
    elif path == PUBLISH_WORKFLOW:
        validate_publish_workflow(path, workflow, builds, errors)


def validate_worktree(root: Path, tag: str | None = None) -> list[str]:
    errors: list[str] = []
    root = root.resolve()
    for relative_path in WORKFLOW_PATHS:
        try:
            workflow = load_mapping(root / relative_path)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        validate_workflow(relative_path, workflow, errors)

    validate_release_metadata(root, errors)

    config_path = root / "addons" / "grott" / "config.yaml"
    try:
        addon_config = load_mapping(config_path)
    except ValueError as exc:
        errors.append(str(exc))
    else:
        addon_version = str(addon_config.get("version", ""))
        if tag is not None and tag != f"v{addon_version}":
            errors.append(
                f"add-on version {addon_version!r} does not match requested tag {tag!r}; expected config version to equal the tag without its leading 'v'"
            )
    return errors


def validate_git_worktree(root: Path, errors: list[str]) -> None:
    root = root.resolve()
    try:
        probe = subprocess.run(
            ("git", "rev-parse", "--is-inside-work-tree"),
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        errors.append(f"{root} is not a Git worktree: {exc}")
        return
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        errors.append(f"{root} is not a Git worktree")
        return

    status = subprocess.run(
        ("git", "status", "--porcelain", "--untracked-files=all"),
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if status.returncode != 0:
        errors.append(
            f"could not inspect Git worktree status for {root}: "
            f"{status.stderr.strip() or 'git status failed'}"
        )
        return
    if status.stdout:
        errors.append(
            "release worktree must be clean, including untracked files; "
            "git status --porcelain --untracked-files=all reported:\n"
            + status.stdout.rstrip()
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help="repository worktree to validate (defaults to this script's repository)",
    )
    parser.add_argument(
        "--check-worktree",
        action="store_true",
        help="require --root to be a clean Git worktree, including untracked files",
    )
    parser.add_argument("--tag", help="optional release tag to validate against add-on metadata")
    args = parser.parse_args(argv)
    if args.tag is not None and not validate_tag_syntax(args.tag):
        parser.error("--tag must be a valid release tag in the form vX.Y.Z[-prerelease] without build metadata or numeric leading zeroes")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    errors: list[str] = []
    if args.check_worktree:
        validate_git_worktree(args.root, errors)
    errors.extend(validate_worktree(args.root, args.tag))
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    suffix = f" for {args.tag}" if args.tag else ""
    print(f"Release worktree invariants are valid{suffix}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
