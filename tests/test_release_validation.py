from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tools import validate_release, verify_release_controls


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
VALIDATOR = ROOT / "tools" / "validate_release.py"
RELEASE_CONTROL_TOOL = ROOT / "tools" / "verify_release_controls.py"
REQUIRED_PLATFORMS = {
    "linux/amd64",
    "linux/arm64",
    "linux/arm/v7",
    "linux/386",
}
PINNED_ACTION = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")
PINNED_TRIVY_IMAGE = (
    "aquasec/trivy:0.72.0@"
    "sha256:cffe3f5161a47a6823fbd23d985795b3ed72a4c806da4c4df16266c02accdd6f"
)
PRERELEASE_STEP_NAME = "Verify or create idempotent GitHub prerelease"
TEST_RELEASE_TAG = "v1.2.3-beta"
TEST_TAG_OBJECT_SHA = "a" * 40
TEST_SOURCE_SHA = "b" * 40
TEST_WRONG_SHA = "c" * 40


def load_workflow(name: str) -> dict:
    with (WORKFLOWS / name).open(encoding="utf-8") as handle:
        workflow = yaml.safe_load(handle)
    assert isinstance(workflow, dict)
    return workflow


def steps(workflow: dict):
    for job_name, job in workflow["jobs"].items():
        for step in job.get("steps", []):
            yield job_name, step


def action_steps(workflow: dict):
    return [step for _, step in steps(workflow) if "uses" in step]


def build_steps(workflow: dict):
    return [
        step
        for _, step in steps(workflow)
        if str(step.get("uses", "")).startswith("docker/build-push-action@")
    ]


def platforms(step: dict) -> set[str]:
    return {
        platform.strip()
        for platform in str(step.get("with", {}).get("platforms", "")).split(",")
        if platform.strip()
    }


def workflow_shell_accepts_tag(tag: str) -> bool:
    workflow = load_workflow("publish-ghcr.yml")
    validation_step = next(
        step
        for _, step in steps(workflow)
        if step.get("name") == "Validate requested release tag"
    )
    script = validation_step["run"]
    pattern_match = re.search(r"^tag_pattern='([^']+)'$", script, re.MULTILINE)
    assert pattern_match, "workflow tag gate must expose one auditable regex"
    assert "numeric prerelease identifiers must not have leading zeroes" in script
    if re.fullmatch(pattern_match.group(1), tag) is None:
        return False
    if "-" not in tag:
        return True
    return all(
        not (
            identifier.isdigit() and len(identifier) > 1 and identifier.startswith("0")
        )
        for identifier in tag.split("-", 1)[1].split(".")
    )


def run_validator(*args: str, root: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATOR), "--root", str(root), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def find_usable_bash() -> str:
    candidates = (
        shutil.which("bash"),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    )
    for candidate in candidates:
        if not candidate or not Path(candidate).is_file():
            continue
        probe = subprocess.run(
            [candidate, "--version"],
            text=True,
            capture_output=True,
            check=False,
        )
        if probe.returncode == 0:
            return candidate
    pytest.skip("a working Bash executable is required for the release-shell contract")


def run_existing_prerelease_guard(
    tmp_path: Path,
    release: dict[str, object],
    *,
    curated_notes: str = "Human-written release notes.\n",
) -> subprocess.CompletedProcess[str]:
    workflow = load_workflow("publish-ghcr.yml")
    release_step = workflow["jobs"]["prerelease"]["steps"][-1]
    mock_jq = tmp_path / "mock_jq.py"
    mock_jq.write_text(
        """\
import json
import sys

arguments = sys.argv[1:]
variables = {}
rawfiles = {}
program = ""
index = 0
while index < len(arguments):
    argument = arguments[index]
    if argument == "--arg":
        variables[arguments[index + 1]] = arguments[index + 2]
        index += 3
    elif argument == "--rawfile":
        rawfiles[arguments[index + 1]] = open(
            arguments[index + 2], encoding="utf-8"
        ).read()
        index += 3
    elif argument.startswith("-"):
        index += 1
    else:
        program = argument
        index += 1

payload = json.loads(sys.stdin.read())
if 'type == "object"' in program:
    required = ("tag_name", "target_commitish", "draft", "prerelease", "body")
    valid = isinstance(payload, dict) and all(
        f'has("{name}")' not in program or name in payload for name in required
    )
elif ".tag_name == $tag" in program:
    valid = payload.get("tag_name") == variables["tag"]
    if ".target_commitish == $sha" in program:
        valid = valid and payload.get("target_commitish") == variables["sha"]
    valid = valid and payload.get("draft") is False
    valid = valid and payload.get("prerelease") is True
    valid = valid and str(payload.get("body") or "") == rawfiles["expected_body"]
elif program.lstrip().startswith("{tag_name"):
    selected = {
        name: payload.get(name)
        for name in ("tag_name", "target_commitish", "draft", "prerelease")
    }
    print(json.dumps(selected, separators=(",", ":")))
    valid = True
else:
    valid = False

raise SystemExit(0 if valid else 1)
""",
        encoding="utf-8",
    )
    shell_prelude = r"""
gh() {
  if [[ "$1" == "api" ]]; then
    if [[ "$*" == *"/contents/"* ]]; then
      printf '%s' "${MOCK_CURATED_NOTES}"
      return 0
    fi
    printf '%s\n' "${MOCK_RELEASE_JSON}"
    return 0
  fi
  echo "unexpected gh mutation: $*" >&2
  return 99
}
jq() {
  "${PYTHON_EXE}" "${MOCK_JQ}" "$@"
}
"""
    environment = os.environ | {
        "GITHUB_REPOSITORY": "Herbertmt978/grott",
        "RELEASE_TAG": "v0.1.10-beta",
        "SOURCE_SHA": "0311742803117428031174280311742803117428",
        "RUNTIME_IMAGE": "ghcr.io/herbertmt978/grott",
        "ADDON_IMAGE": "ghcr.io/herbertmt978/grott-ha-docker",
        "RUNTIME_DIGEST": "sha256:" + "a" * 64,
        "ADDON_DIGEST": "sha256:" + "b" * 64,
        "MOCK_RELEASE_JSON": json.dumps(release),
        "MOCK_CURATED_NOTES": curated_notes,
        "MOCK_JQ": mock_jq.as_posix(),
        "PYTHON_EXE": Path(sys.executable).as_posix(),
    }
    return subprocess.run(
        [find_usable_bash(), "-c", shell_prelude + release_step["run"]],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def run_git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return completed.stdout.strip()


def create_release_git_repository(
    destination: Path, *, annotated: bool = True
) -> tuple[Path, str, str, str]:
    repository = destination / "release-repository"
    repository.mkdir()
    run_git(repository, "init")
    run_git(repository, "config", "user.name", "Release Test")
    run_git(repository, "config", "user.email", "release-test@example.invalid")
    payload = repository / "payload.txt"
    payload.write_text("release\n", encoding="utf-8")
    run_git(repository, "add", "payload.txt")
    run_git(repository, "commit", "-m", "Release source")
    release_sha = run_git(repository, "rev-parse", "HEAD")
    tag_arguments = (
        ("tag", "-a", "v1.2.3-beta", "-m", "Release v1.2.3-beta")
        if annotated
        else ("tag", "v1.2.3-beta")
    )
    run_git(repository, *tag_arguments)

    payload.write_text("release\nnext\n", encoding="utf-8")
    run_git(repository, "add", "payload.txt")
    run_git(repository, "commit", "-m", "Later protected-branch commit")
    other_sha = run_git(repository, "rev-parse", "HEAD")
    release_tree = run_git(repository, "rev-parse", f"{release_sha}^{{tree}}")
    orphan_sha = run_git(repository, "commit-tree", release_tree, "-m", "Orphan")
    run_git(repository, "checkout", "--detach", "v1.2.3-beta")
    return repository, release_sha, other_sha, orphan_sha


def run_source_verification(
    repository: Path, dispatch_sha: str
) -> subprocess.CompletedProcess[str]:
    workflow = load_workflow("publish-ghcr.yml")
    source_step = next(
        step
        for step in workflow["jobs"]["validate"]["steps"]
        if step.get("name") == "Verify checked-out tag and source commit"
    )
    output_path = repository / "github-output.txt"
    environment = os.environ | {
        "RELEASE_TAG": "v1.2.3-beta",
        "DISPATCH_SHA": dispatch_sha,
        "GITHUB_OUTPUT": output_path.as_posix(),
    }
    return subprocess.run(
        [find_usable_bash(), "-c", source_step["run"]],
        cwd=repository,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def run_remote_revalidation(
    remote_refs: str,
    branch_refs: str | None = None,
    *,
    step_name: str = "Revalidate remote tag before package login",
    event_default_branch: str = "master",
    live_default_branch: str = "master",
    live_repository: str = "Herbertmt978/grott",
    repository_state: str | None = None,
) -> subprocess.CompletedProcess[str]:
    workflow = load_workflow("publish-ghcr.yml")
    job_name = (
        "prerelease"
        if step_name == "Revalidate remote tag before prerelease"
        else "publish"
    )
    revalidation_step = next(
        step
        for step in workflow["jobs"][job_name]["steps"]
        if step.get("name") == step_name
    )
    shell_prelude = r"""
gh() {
  if [[ "$1" == "api" ]]; then
    printf '%s' "${MOCK_REPOSITORY_STATE}"
    return 0
  fi
  echo "unexpected gh command: $*" >&2
  return 98
}
git() {
  if [[ "$1" == "check-ref-format" ]]; then
    command git "$@"
    return $?
  fi
  if [[ "$1" == "ls-remote" ]]; then
    if [[ "$*" == *"refs/heads/"* ]]; then
      printf '%s' "${MOCK_BRANCH_REFS}"
    else
      printf '%s' "${MOCK_REMOTE_REFS}"
    fi
    return 0
  fi
  echo "unexpected git command: $*" >&2
  return 99
}
"""
    environment = os.environ | {
        "RELEASE_TAG": TEST_RELEASE_TAG,
        "SOURCE_SHA": TEST_SOURCE_SHA,
        "DEFAULT_BRANCH": event_default_branch,
        "VALIDATED_DEFAULT_BRANCH": event_default_branch,
        "GH_TOKEN": "synthetic-token",
        "GITHUB_REPOSITORY": "Herbertmt978/grott",
        "REMOTE_URL": "https://example.invalid/repository.git",
        "MOCK_REMOTE_REFS": remote_refs,
        "MOCK_REPOSITORY_STATE": repository_state
        if repository_state is not None
        else f"{live_repository}\t{live_default_branch}\n",
        "MOCK_BRANCH_REFS": branch_refs
        if branch_refs is not None
        else f"{TEST_SOURCE_SHA}\trefs/heads/{live_default_branch}\n",
    }
    return subprocess.run(
        [find_usable_bash(), "-c", shell_prelude + revalidation_step["run"]],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def run_final_ref_guard(mode: str) -> subprocess.CompletedProcess[str]:
    workflow = load_workflow("publish-ghcr.yml")
    guard_step = next(
        step
        for step in workflow["jobs"]["publish"]["steps"]
        if step.get("name") == "Guard final tags against conflicting digests"
    )
    shell_prelude = r"""
docker() {
  local ref="$4"
  local digest="sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  if [[ "${ref}" == *"grott-ha-docker"* ]]; then
    digest="sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  fi
  case "${MOCK_MODE}" in
    exact-absence)
      printf 'ERROR: %s: not found\n' "${ref}" >&2
      return 1
      ;;
    equal)
      printf '%s\n' "${digest}"
      ;;
    conflict)
      printf 'sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc\n'
      ;;
    ambiguous-not-found)
      printf 'ERROR: registry gateway not found while resolving request\n' >&2
      return 1
      ;;
    authentication)
      printf 'ERROR: denied: requested access to the resource is denied\n' >&2
      return 1
      ;;
    timeout)
      printf 'ERROR: request timed out\n' >&2
      return 1
      ;;
    malformed)
      printf 'not-json\n'
      ;;
    *)
      return 99
      ;;
  esac
}
jq() {
  if [[ "${MOCK_MODE}" == "malformed" ]]; then
    cat >/dev/null
    printf 'parse error\n' >&2
    return 4
  fi
  cat
}
"""
    environment = os.environ | {
        "MOCK_MODE": mode,
        "RELEASE_TAG": "v0.1.10-beta",
        "RELEASE_VERSION": "0.1.10-beta",
        "RUNTIME_IMAGE": "ghcr.io/herbertmt978/grott",
        "ADDON_IMAGE": "ghcr.io/herbertmt978/grott-ha-docker",
        "RUNTIME_DIGEST": "sha256:" + "a" * 64,
        "ADDON_DIGEST": "sha256:" + "b" * 64,
    }
    return subprocess.run(
        [find_usable_bash(), "-c", shell_prelude + guard_step["run"]],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def run_public_release_gate(
    tmp_path: Path, mode: str
) -> subprocess.CompletedProcess[str]:
    workflow = load_workflow("publish-ghcr.yml")
    gate_step = next(
        step
        for step in workflow["jobs"]["gate"]["steps"]
        if step.get("name") == "Verify public release controls"
    )
    source_sha = "d" * 40
    fixtures: dict[str, object] = {
        "repository.json": {
            "full_name": "Herbertmt978/grott",
            "default_branch": "master",
        },
        "default-ref.json": {
            "ref": "refs/heads/master",
            "object": {"type": "commit", "sha": source_sha},
        },
        "ci-runs.json": {
            "workflow_runs": [
                {
                    "head_sha": source_sha,
                    "head_branch": "master",
                    "event": "push",
                    "status": "completed",
                    "conclusion": "success",
                }
            ]
        },
        "branch-rules.json": [
            [
                {"type": "deletion"},
                {"type": "non_fast_forward"},
                {
                    "type": "pull_request",
                    "parameters": {
                        "required_approving_review_count": 1,
                        "dismiss_stale_reviews_on_push": True,
                        "require_last_push_approval": True,
                        "required_review_thread_resolution": True,
                    },
                },
                {
                    "type": "required_status_checks",
                    "parameters": {
                        "strict_required_status_checks_policy": True,
                        "required_status_checks": [{"context": "test"}],
                    },
                },
            ]
        ],
        "environment.json": {
            "name": "release",
            "protection_rules": [
                {
                    "type": "required_reviewers",
                    "prevent_self_review": True,
                    "reviewers": [{"type": "User", "reviewer": {"id": 1}}],
                }
            ],
            "deployment_branch_policy": {
                "protected_branches": True,
                "custom_branch_policies": False,
            },
        },
        "tag-rulesets.json": [[{"id": 42, "target": "tag", "enforcement": "active"}]],
        "tag-ruleset-42.json": {
            "id": 42,
            "target": "tag",
            "enforcement": "active",
            "conditions": {
                "ref_name": {"include": ["refs/tags/v*"], "exclude": []}
            },
            "rules": [
                {"type": "creation"},
                {"type": "update"},
                {"type": "deletion"},
            ],
        },
    }
    if mode == "ci-failure":
        fixtures["ci-runs.json"]["workflow_runs"][0]["conclusion"] = "failure"
    elif mode == "branch-review-missing":
        fixtures["branch-rules.json"][0] = [
            rule
            for rule in fixtures["branch-rules.json"][0]
            if rule.get("type") != "pull_request"
        ]
    elif mode == "branch-review-count-malformed":
        fixtures["branch-rules.json"][0][2]["parameters"][
            "required_approving_review_count"
        ] = "1"
    elif mode == "branch-status-missing":
        fixtures["branch-rules.json"][0] = [
            rule
            for rule in fixtures["branch-rules.json"][0]
            if rule.get("type") != "required_status_checks"
        ]
    elif mode == "environment-review-missing":
        fixtures["environment.json"]["protection_rules"] = []
    elif mode == "environment-self-review":
        fixtures["environment.json"]["protection_rules"][0][
            "prevent_self_review"
        ] = False
    elif mode == "environment-reviewer-malformed":
        fixtures["environment.json"]["protection_rules"][0]["reviewers"] = [{}]
    elif mode == "environment-unrestricted":
        fixtures["environment.json"]["deployment_branch_policy"] = None
    elif mode == "tag-rules-missing":
        fixtures["tag-ruleset-42.json"]["rules"] = [
            {"type": "creation"},
            {"type": "update"},
        ]
    elif mode == "tag-ruleset-evaluate":
        fixtures["tag-ruleset-42.json"]["enforcement"] = "evaluate"
    elif mode == "tag-ruleset-id-mismatch":
        fixtures["tag-ruleset-42.json"]["id"] = 43
    elif mode == "tag-exact-ref-only":
        fixtures["tag-ruleset-42.json"]["conditions"]["ref_name"]["include"] = [
            "refs/tags/v0.1.10-beta"
        ]
    elif mode == "branch-moved":
        fixtures["default-ref.json"]["object"]["sha"] = "e" * 40
    elif mode == "repository-mismatch":
        fixtures["repository.json"]["full_name"] = "another-owner/grott"
    elif mode not in {"valid", "environment-api-error", "malformed-json"}:
        raise AssertionError(f"unknown gate fixture mode: {mode}")

    fixture_dir = tmp_path / "gate-fixtures"
    fixture_dir.mkdir()
    for name, payload in fixtures.items():
        path = fixture_dir / name
        if mode == "malformed-json" and name == "environment.json":
            path.write_text("not-json\n", encoding="utf-8")
        else:
            path.write_text(json.dumps(payload), encoding="utf-8")

    shell_prelude = r"""
python() {
  "${PYTHON_EXE}" "$@"
}
gh() {
  [[ "$1" == "api" ]] || return 99
  case "$*" in
    *"/git/ref/heads/"*) cat "${MOCK_DIR}/default-ref.json" ;;
    *"/actions/workflows/ci.yml/runs"*) cat "${MOCK_DIR}/ci-runs.json" ;;
    *"/rules/branches/"*) cat "${MOCK_DIR}/branch-rules.json" ;;
    *"/environments/release"*)
      if [[ "${MOCK_MODE}" == "environment-api-error" ]]; then
        printf 'not found\n' >&2
        return 1
      fi
      cat "${MOCK_DIR}/environment.json"
      ;;
    *"/rulesets/42"*) cat "${MOCK_DIR}/tag-ruleset-42.json" ;;
    *"/rulesets"*) cat "${MOCK_DIR}/tag-rulesets.json" ;;
    *"repos/Herbertmt978/grott"*) cat "${MOCK_DIR}/repository.json" ;;
    *) printf 'unexpected gh api call: %s\n' "$*" >&2; return 99 ;;
  esac
}
"""
    environment = os.environ | {
        "PYTHON_EXE": Path(sys.executable).as_posix(),
        "MOCK_DIR": fixture_dir.as_posix(),
        "MOCK_MODE": mode,
        "GITHUB_REPOSITORY": "Herbertmt978/grott",
        "DEFAULT_BRANCH": "master",
        "RELEASE_TAG": "v0.1.10-beta",
        "SOURCE_SHA": source_sha,
        "GH_TOKEN": "test-token-not-a-secret",
    }
    return subprocess.run(
        [find_usable_bash(), "-c", shell_prelude + gate_step["run"]],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def copy_release_inputs(destination: Path) -> None:
    shutil.copytree(ROOT / ".github", destination / ".github")
    shutil.copytree(ROOT / "addons", destination / "addons")
    shutil.copytree(ROOT / "docker", destination / "docker")
    shutil.copytree(
        ROOT / "docs",
        destination / "docs",
        ignore=shutil.ignore_patterns("aegis"),
    )
    shutil.copytree(ROOT / "examples", destination / "examples")
    shutil.copytree(
        ROOT / "tools",
        destination / "tools",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    for filename in ("README.md", "RELEASING.md", "grott.py"):
        shutil.copy2(ROOT / filename, destination / filename)


def initialize_clean_release_worktree(destination: Path) -> None:
    copy_release_inputs(destination)
    run_git(destination, "init")
    run_git(destination, "config", "user.name", "Release Test")
    run_git(destination, "config", "user.email", "release-test@example.invalid")
    run_git(destination, "add", "--all")
    run_git(destination, "commit", "-m", "Release worktree fixture")
    assert run_git(destination, "status", "--porcelain", "--untracked-files=all") == ""


def workflow_trigger(workflow: dict) -> dict:
    trigger = workflow.get("on", workflow.get(True))
    assert isinstance(trigger, dict)
    return trigger


def mutate_publish_workflow(destination: Path, mutation) -> None:
    copy_release_inputs(destination)
    publish_path = destination / ".github" / "workflows" / "publish-ghcr.yml"
    workflow = validate_release.load_mapping(publish_path)
    mutation(workflow)
    publish_path.write_text(yaml.safe_dump(workflow, sort_keys=False), encoding="utf-8")


@pytest.mark.parametrize("workflow_name", ["ci.yml", "publish-ghcr.yml"])
def test_workflow_actions_are_pinned_to_full_commit_shas(workflow_name: str) -> None:
    workflow = load_workflow(workflow_name)
    mutable = [
        step["uses"]
        for step in action_steps(workflow)
        if not PINNED_ACTION.fullmatch(step["uses"])
    ]
    assert not mutable, f"mutable action refs: {mutable}"


@pytest.mark.parametrize("workflow_name", ["ci.yml", "publish-ghcr.yml"])
def test_checkout_never_persists_credentials(workflow_name: str) -> None:
    workflow = load_workflow(workflow_name)
    checkouts = [
        step
        for step in action_steps(workflow)
        if str(step["uses"]).startswith("actions/checkout@")
    ]
    assert checkouts
    assert all(
        step.get("with", {}).get("persist-credentials") is False for step in checkouts
    )


def test_ci_builds_both_images_from_root_for_every_advertised_platform() -> None:
    builds = build_steps(load_workflow("ci.yml"))
    by_file = {step.get("with", {}).get("file"): step for step in builds}

    assert set(by_file) == {"docker/dockerfile", "addons/grott/Dockerfile"}
    for step in by_file.values():
        assert step["with"].get("context") == "."
        assert platforms(step) == REQUIRED_PLATFORMS
        assert step["with"].get("push") is False


def test_workflows_do_not_advertise_clean_checkout_diff_checks() -> None:
    for workflow_name in ("ci.yml", "publish-ghcr.yml"):
        workflow = load_workflow(workflow_name)
        assert all(
            step.get("name") != "Check patch whitespace" for _, step in steps(workflow)
        )


def test_publish_keeps_manual_input_out_of_shell_and_validates_before_checkout() -> (
    None
):
    workflow = load_workflow("publish-ghcr.yml")
    ordered_steps = list(steps(workflow))
    checkout_index = next(
        index
        for index, (_, step) in enumerate(ordered_steps)
        if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    validation_index, (_, validation_step) = next(
        (index, item)
        for index, item in enumerate(ordered_steps)
        if "Validate requested release tag" in item[1].get("name", "")
    )

    assert validation_index < checkout_index
    assert "inputs.tag" in str(validation_step.get("env", {}).get("REQUESTED_TAG", ""))
    assert "REQUESTED_TAG" in validation_step.get("run", "")
    assert "=~" in validation_step.get("run", "")
    for _, step in ordered_steps:
        assert "${{ inputs.tag }}" not in str(step.get("run", ""))


def test_publish_does_not_restore_dependency_caches_before_package_write() -> None:
    workflow = load_workflow("publish-ghcr.yml")
    setup_python = next(
        step
        for _, step in steps(workflow)
        if str(step.get("uses", "")).startswith("actions/setup-python@")
    )
    assert "cache" not in setup_python.get("with", {})


def test_publish_workflow_is_manual_only_serialized_by_requested_tag() -> None:
    workflow = load_workflow("publish-ghcr.yml")
    assert set(workflow_trigger(workflow)) == {"workflow_dispatch"}
    assert "inputs.tag" in workflow["concurrency"]["group"]
    assert workflow["concurrency"]["cancel-in-progress"] is False


def test_publish_dispatch_tag_description_is_version_neutral() -> None:
    workflow = load_workflow("publish-ghcr.yml")
    tag_input = workflow_trigger(workflow)["workflow_dispatch"]["inputs"]["tag"]

    assert "existing protected release tag" in tag_input["description"].lower()
    assert not re.search(r"v?\d+\.\d+\.\d+", tag_input["description"])


def test_prerelease_records_verified_immutable_manifests_and_platforms() -> None:
    workflow = load_workflow("publish-ghcr.yml")
    publish_job = workflow["jobs"]["publish"]
    prerelease_job = workflow["jobs"]["prerelease"]
    create_step = next(
        step
        for step in prerelease_job["steps"]
        if step.get("name") == PRERELEASE_STEP_NAME
    )

    assert publish_job["outputs"]["runtime_digest"] == (
        "${{ steps.runtime_build.outputs.digest }}"
    )
    assert publish_job["outputs"]["addon_digest"] == (
        "${{ steps.addon_build.outputs.digest }}"
    )
    assert create_step["env"]["RUNTIME_IMAGE"] == "${{ env.RUNTIME_IMAGE }}"
    assert create_step["env"]["ADDON_IMAGE"] == "${{ env.ADDON_IMAGE }}"
    assert create_step["env"]["RUNTIME_DIGEST"] == (
        "${{ needs.publish.outputs.runtime_digest }}"
    )
    assert create_step["env"]["ADDON_DIGEST"] == (
        "${{ needs.publish.outputs.addon_digest }}"
    )

    script = create_step["run"]
    for token in (
        "digest_pattern='^sha256:[0-9a-f]{64}$'",
        '[[ ! "${RUNTIME_DIGEST}" =~ ${digest_pattern}',
        '[[ ! "${ADDON_DIGEST}" =~ ${digest_pattern}',
        'exit 1',
        '${RUNTIME_IMAGE}@${RUNTIME_DIGEST}',
        '${ADDON_IMAGE}@${ADDON_DIGEST}',
        'linux/amd64, linux/arm64, linux/arm/v7, linux/386',
        '--notes-file "${release_body_file}"',
    ):
        assert token in script


def test_prerelease_fetches_curated_notes_from_verified_source_and_publishes_exact_body() -> (
    None
):
    workflow = load_workflow("publish-ghcr.yml")
    prerelease_job = workflow["jobs"]["prerelease"]
    assert len(prerelease_job["steps"]) == 2
    create_step = next(
        step
        for step in prerelease_job["steps"]
        if step.get("name") == PRERELEASE_STEP_NAME
    )
    script = create_step["run"]

    for token in (
        'release_notes_path="docs/releases/${RELEASE_TAG}.md"',
        'repos/${GITHUB_REPOSITORY}/contents/${release_notes_path}',
        "gh api --method GET",
        '-f ref="${SOURCE_SHA}"',
        "application/vnd.github.raw+json",
        '[[ ! -s "${curated_notes_file}" ]]',
        "curated release notes are missing or blank",
        'cat "${curated_notes_file}"',
        '--notes-file "${release_body_file}"',
        '--rawfile expected_body "${release_body_file}"',
        '(.body // "") == $expected_body',
    ):
        assert token in script

    assert "--generate-notes" not in script
    assert '--notes "${release_notes}"' not in script
    assert "startswith($prefix)" not in script


def test_prerelease_fails_closed_when_curated_notes_are_blank(tmp_path: Path) -> None:
    immutable_prefix = "\n".join(
        (
            "## Immutable release images",
            "",
            "- Runtime: ghcr.io/herbertmt978/grott@sha256:" + "a" * 64,
            "- Home Assistant add-on: "
            "ghcr.io/herbertmt978/grott-ha-docker@sha256:"
            + "b" * 64,
            "- Platforms: linux/amd64, linux/arm64, linux/arm/v7, linux/386",
        )
    )
    release = {
        "tag_name": "v0.1.10-beta",
        "target_commitish": "master",
        "draft": False,
        "prerelease": True,
        "body": immutable_prefix,
    }

    completed = run_existing_prerelease_guard(
        tmp_path, release, curated_notes=" \n\t\n"
    )

    assert completed.returncode != 0
    assert "curated release notes are missing or blank" in completed.stderr


def test_prerelease_is_idempotent_and_verifies_exact_existing_release() -> None:
    workflow = load_workflow("publish-ghcr.yml")
    prerelease_job = workflow["jobs"]["prerelease"]
    release_step = prerelease_job["steps"][-1]
    script = release_step["run"]

    for token in (
        "lookup_release()",
        "verify_release()",
        "verify_final_release()",
        'gh api "repos/${GITHUB_REPOSITORY}/releases/tags/${RELEASE_TAG}"',
        'readonly LOOKUP_ABSENT=4',
        'readonly LOOKUP_FAILED=10',
        'readonly LOOKUP_AMBIGUOUS=11',
        '.tag_name == $tag',
        '.draft == false',
        '.prerelease == true',
        '--rawfile expected_body "${release_body_file}"',
        '(.body // "") == $expected_body',
        'return "${LOOKUP_FAILED}"',
        'return "${LOOKUP_ABSENT}"',
        'return "${LOOKUP_AMBIGUOUS}"',
        'release_was_absent=false',
        'release_was_absent=true',
        'create_status=$?',
        'verify_final_release',
    ):
        assert token in script

    assert "target_commitish" not in script
    assert '--arg sha "${SOURCE_SHA}"' not in script
    assert script.count("verify_release ") >= 2
    assert "Only an explicit, successfully queried absence permits creation" in script
    assert "An exact release now exists; accepting the idempotent retry" in script


@pytest.mark.parametrize(
    ("override", "accepted"),
    [
        pytest.param({}, True, id="branch-target-commitish"),
        pytest.param({"tag_name": "v9.9.9-beta"}, False, id="wrong-tag"),
        pytest.param({"draft": True}, False, id="draft-release"),
        pytest.param({"prerelease": False}, False, id="not-prerelease"),
        pytest.param({"body": "mutable refs only"}, False, id="wrong-body"),
    ],
)
def test_existing_prerelease_guard_accepts_branch_target_only_when_release_matches(
    tmp_path: Path, override: dict[str, object], accepted: bool
) -> None:
    curated_notes = "Human-written release notes.\n"
    immutable_prefix = "\n".join(
        (
            "## Immutable release images",
            "",
            "- Runtime: ghcr.io/herbertmt978/grott@sha256:" + "a" * 64,
            "- Home Assistant add-on: "
            "ghcr.io/herbertmt978/grott-ha-docker@sha256:"
            + "b" * 64,
            "- Platforms: linux/amd64, linux/arm64, linux/arm/v7, linux/386",
        )
    )
    body = immutable_prefix + "\n\n" + curated_notes
    release: dict[str, object] = {
        "tag_name": "v0.1.10-beta",
        "target_commitish": "master",
        "draft": False,
        "prerelease": True,
        "body": body,
    }
    release.update(override)

    completed = run_existing_prerelease_guard(
        tmp_path, release, curated_notes=curated_notes
    )

    assert (completed.returncode == 0) is accepted, completed.stdout + completed.stderr
    assert "unexpected gh mutation" not in completed.stderr


def test_validation_is_split_from_package_write_authority() -> None:
    workflow = load_workflow("publish-ghcr.yml")
    validate_job = workflow["jobs"]["validate"]
    gate_job = workflow["jobs"]["gate"]
    publish_job = workflow["jobs"]["publish"]
    prerelease_job = workflow["jobs"]["prerelease"]

    assert validate_job["permissions"] == {"contents": "read"}
    assert gate_job["permissions"] == {"actions": "read", "contents": "read"}
    assert publish_job["permissions"] == {"contents": "read", "packages": "write"}
    assert prerelease_job["permissions"] == {"contents": "write"}
    assert gate_job["needs"] == "validate"
    assert publish_job["needs"] == ["validate", "gate"]
    assert prerelease_job["needs"] == "publish"
    assert publish_job["environment"] == "release"
    assert prerelease_job["environment"] == "release"
    assert "github.event.repository.default_branch" in validate_job["if"]
    assert "github.ref_protected" in validate_job["if"]

    validate_runs = "\n".join(
        str(step.get("run", "")) for step in validate_job["steps"]
    )
    publish_runs = "\n".join(str(step.get("run", "")) for step in publish_job["steps"])
    assert "pytest" in validate_runs and "validate_release.py" in validate_runs
    assert "pytest" not in publish_runs and "pip install" not in publish_runs

    gate_step = next(
        step
        for step in gate_job["steps"]
        if step.get("name") == "Verify public release controls"
    )
    gate_script = gate_step["run"]
    for token in (
        "actions/workflows/ci.yml/runs",
        "rules/branches",
        "rulesets",
        "environments/release",
        "verify_release_controls.py list-ruleset-ids",
        "verify_release_controls.py verify",
        "sha256sum --check --strict",
    ):
        assert token in gate_script

    gate_checkout = next(
        step
        for step in gate_job["steps"]
        if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    assert gate_checkout["with"]["ref"] == "${{ needs.validate.outputs.source_sha }}"
    assert gate_checkout["with"]["persist-credentials"] is False

    release_control_source = RELEASE_CONTROL_TOOL.read_text(encoding="utf-8")
    for token in (
        "required_approving_review_count",
        "strict_required_status_checks_policy",
        '"refs/tags/v*"',
        "required_reviewers",
        "prevent_self_review",
        "protected_branches",
    ):
        assert token in release_control_source

    publish_checkout = next(
        step
        for step in publish_job["steps"]
        if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    assert publish_checkout["with"]["ref"] == "${{ needs.validate.outputs.source_sha }}"
    assert publish_checkout["with"]["persist-credentials"] is False


def test_release_control_tool_hash_matches_workflow_and_validator() -> None:
    workflow = load_workflow("publish-ghcr.yml")
    gate_step = next(
        step
        for step in workflow["jobs"]["gate"]["steps"]
        if step.get("name") == "Verify public release controls"
    )
    match = re.search(r'^\s*gate_tool_sha256="([0-9a-f]{64})"$', gate_step["run"], re.MULTILINE)
    assert match is not None
    actual = hashlib.sha256(RELEASE_CONTROL_TOOL.read_bytes()).hexdigest()

    assert match.group(1) == actual
    assert validate_release.EXPECTED_RELEASE_CONTROL_TOOL_SHA256 == actual


def test_validator_rejects_modified_release_control_tool(tmp_path: Path) -> None:
    copy_release_inputs(tmp_path)
    tool_path = tmp_path / "tools" / "verify_release_controls.py"
    tool_path.write_text(
        tool_path.read_text(encoding="utf-8") + "\n# unreviewed change\n",
        encoding="utf-8",
    )

    result = run_validator(root=tmp_path)

    assert result.returncode == 1
    assert "release-control verifier does not match its reviewed SHA-256" in result.stderr


@pytest.mark.parametrize(
    ("mode", "accepted", "diagnostic"),
    (
        ("valid", True, "Public release controls verified"),
        ("ci-failure", False, "no successful protected-branch run"),
        ("branch-review-missing", False, "pull-request review policy"),
        ("branch-review-count-malformed", False, "pull-request review policy"),
        ("branch-status-missing", False, "strict required CI status check"),
        ("environment-review-missing", False, "independent reviewer"),
        ("environment-self-review", False, "independent reviewer"),
        ("environment-reviewer-malformed", False, "independent reviewer"),
        ("environment-unrestricted", False, "protected branches only"),
        ("environment-api-error", False, "not found"),
        ("tag-rules-missing", False, "no active release-tag ruleset"),
        ("tag-ruleset-evaluate", False, "no active release-tag ruleset"),
        ("tag-ruleset-id-mismatch", False, "no active release-tag ruleset"),
        ("tag-exact-ref-only", False, "no active release-tag ruleset"),
        ("branch-moved", False, "default branch no longer resolves"),
        ("repository-mismatch", False, "repository identity does not match"),
        ("malformed-json", False, "environment.json is missing or invalid JSON"),
    ),
)
def test_public_release_control_gate_fails_closed(
    tmp_path: Path, mode: str, accepted: bool, diagnostic: str
) -> None:
    completed = run_public_release_gate(tmp_path, mode)

    assert (completed.returncode == 0) is accepted, completed.stdout + completed.stderr
    assert diagnostic in completed.stdout + completed.stderr


def test_validate_job_preserves_and_exports_exact_protected_dispatch_sha() -> None:
    workflow = load_workflow("publish-ghcr.yml")
    validate_job = workflow["jobs"]["validate"]
    validate_steps = validate_job["steps"]
    preserve_index = next(
        (
            index
            for index, step in enumerate(validate_steps)
            if step.get("name") == "Preserve protected workflow dispatch SHA"
        ),
        None,
    )
    checkout_index = next(
        index
        for index, step in enumerate(validate_steps)
        if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    source_index = next(
        index
        for index, step in enumerate(validate_steps)
        if step.get("name") == "Verify checked-out tag and source commit"
    )
    assert preserve_index is not None
    preserve_step = validate_steps[preserve_index]
    source_step = validate_steps[source_index]

    assert preserve_index < checkout_index < source_index
    assert preserve_step["id"] == "dispatch_source"
    assert preserve_step["env"]["DISPATCH_SHA"] == "${{ github.sha }}"
    assert "printf 'sha=%s\\n' \"${DISPATCH_SHA}\"" in preserve_step["run"]
    assert source_step["env"]["DISPATCH_SHA"] == (
        "${{ steps.dispatch_source.outputs.sha }}"
    )
    assert '[[ "${tag_sha}" != "${DISPATCH_SHA}" ]]' in source_step["run"]
    assert "merge-base --is-ancestor" not in source_step["run"]
    assert "printf 'sha=%s\\n' \"${DISPATCH_SHA}\"" in source_step["run"]
    assert validate_job["outputs"]["source_sha"] == "${{ steps.source.outputs.sha }}"


@pytest.mark.parametrize(
    ("dispatch_kind", "accepted"),
    [
        pytest.param("exact", True, id="exact-dispatch-sha"),
        pytest.param("other", False, id="other-protected-branch-commit"),
        pytest.param("orphan", False, id="orphan-commit"),
    ],
)
def test_source_verification_requires_exact_dispatch_sha(
    tmp_path: Path, dispatch_kind: str, accepted: bool
) -> None:
    repository, release_sha, other_sha, orphan_sha = create_release_git_repository(
        tmp_path
    )
    dispatch_sha = {
        "exact": release_sha,
        "other": other_sha,
        "orphan": orphan_sha,
    }[dispatch_kind]

    completed = run_source_verification(repository, dispatch_sha)

    assert (completed.returncode == 0) is accepted, completed.stdout + completed.stderr
    if accepted:
        output = (repository / "github-output.txt").read_text(encoding="utf-8")
        assert output == f"sha={dispatch_sha}\n"


@pytest.mark.parametrize(
    ("annotated", "accepted"),
    [
        pytest.param(True, True, id="annotated-tag"),
        pytest.param(False, False, id="lightweight-tag"),
    ],
)
def test_checked_out_release_tag_must_be_annotated(
    tmp_path: Path, annotated: bool, accepted: bool
) -> None:
    repository, release_sha, _, _ = create_release_git_repository(
        tmp_path, annotated=annotated
    )

    completed = run_source_verification(repository, release_sha)

    assert (completed.returncode == 0) is accepted, completed.stdout + completed.stderr


def remote_ref(sha: str, suffix: str = "") -> tuple[str, str]:
    return sha, f"refs/tags/{TEST_RELEASE_TAG}{suffix}"


@pytest.mark.parametrize(
    ("refs", "accepted"),
    [
        pytest.param(
            [remote_ref(TEST_TAG_OBJECT_SHA), remote_ref(TEST_SOURCE_SHA, "^{}")],
            True,
            id="annotated-direct-and-peeled",
        ),
        pytest.param(
            [remote_ref(TEST_SOURCE_SHA)],
            False,
            id="lightweight-missing-peeled",
        ),
        pytest.param(
            [remote_ref(TEST_TAG_OBJECT_SHA)],
            False,
            id="annotated-missing-peeled",
        ),
        pytest.param(
            [remote_ref(TEST_SOURCE_SHA, "^{}")],
            False,
            id="missing-direct-tag-object",
        ),
        pytest.param(
            [
                remote_ref(TEST_TAG_OBJECT_SHA),
                remote_ref(TEST_WRONG_SHA),
                remote_ref(TEST_SOURCE_SHA, "^{}"),
            ],
            False,
            id="duplicate-direct-ref",
        ),
        pytest.param(
            [
                remote_ref(TEST_TAG_OBJECT_SHA),
                remote_ref(TEST_SOURCE_SHA, "^{}"),
                remote_ref(TEST_SOURCE_SHA, "^{}"),
            ],
            False,
            id="duplicate-peeled-ref",
        ),
        pytest.param(
            [remote_ref(TEST_TAG_OBJECT_SHA), remote_ref(TEST_WRONG_SHA, "^{}")],
            False,
            id="wrong-peeled-sha",
        ),
        pytest.param(
            [remote_ref(TEST_SOURCE_SHA), remote_ref(TEST_SOURCE_SHA, "^{}")],
            False,
            id="direct-equals-peeled",
        ),
    ],
)
def test_remote_revalidation_requires_one_annotated_tag_object_and_peeled_commit(
    refs: list[tuple[str, str]], accepted: bool
) -> None:
    remote_refs = "".join(f"{sha}\t{name}\n" for sha, name in refs)

    completed = run_remote_revalidation(remote_refs)

    assert (completed.returncode == 0) is accepted, completed.stdout + completed.stderr


@pytest.mark.parametrize(
    ("branch_refs", "accepted"),
    [
        pytest.param(
            f"{TEST_SOURCE_SHA}\trefs/heads/master\n",
            True,
            id="exact-default-branch-tip",
        ),
        pytest.param("", False, id="missing-default-branch"),
        pytest.param(
            f"{TEST_WRONG_SHA}\trefs/heads/master\n",
            False,
            id="moved-default-branch",
        ),
        pytest.param(
            f"{TEST_SOURCE_SHA}\trefs/heads/master\n"
            f"{TEST_SOURCE_SHA}\trefs/heads/master\n",
            False,
            id="duplicate-default-branch-ref",
        ),
        pytest.param(
            f"{TEST_SOURCE_SHA}\trefs/heads/other\n",
            False,
            id="unexpected-default-branch-ref",
        ),
        pytest.param(
            f"{TEST_SOURCE_SHA}\trefs/heads/master\textra\n",
            False,
            id="malformed-default-branch-ref",
        ),
    ],
)
def test_remote_revalidation_requires_exact_default_branch_tip(
    branch_refs: str, accepted: bool
) -> None:
    tag_refs = (
        f"{TEST_TAG_OBJECT_SHA}\trefs/tags/{TEST_RELEASE_TAG}\n"
        f"{TEST_SOURCE_SHA}\trefs/tags/{TEST_RELEASE_TAG}^{{}}\n"
    )

    completed = run_remote_revalidation(tag_refs, branch_refs)

    assert (completed.returncode == 0) is accepted, completed.stdout + completed.stderr


@pytest.mark.parametrize(
    "step_name",
    [
        "Revalidate remote tag before package login",
        "Revalidate remote tag before final promotion",
        "Revalidate remote tag before prerelease",
    ],
)
@pytest.mark.parametrize(
    ("live_default_branch", "branch_refs", "accepted"),
    [
        pytest.param(
            "master",
            f"{TEST_SOURCE_SHA}\trefs/heads/master\n",
            True,
            id="validated-default-unchanged-at-source",
        ),
        pytest.param(
            "release",
            f"{TEST_SOURCE_SHA}\trefs/heads/release\n",
            False,
            id="renamed-default-at-source-still-rejected",
        ),
        pytest.param(
            "release",
            f"{TEST_SOURCE_SHA}\trefs/heads/master\n",
            False,
            id="stale-event-default-still-at-source",
        ),
    ],
)
def test_remote_revalidation_queries_live_default_branch_at_every_boundary(
    step_name: str,
    live_default_branch: str,
    branch_refs: str,
    accepted: bool,
) -> None:
    tag_refs = (
        f"{TEST_TAG_OBJECT_SHA}\trefs/tags/{TEST_RELEASE_TAG}\n"
        f"{TEST_SOURCE_SHA}\trefs/tags/{TEST_RELEASE_TAG}^{{}}\n"
    )

    completed = run_remote_revalidation(
        tag_refs,
        branch_refs,
        step_name=step_name,
        event_default_branch="master",
        live_default_branch=live_default_branch,
    )

    assert (completed.returncode == 0) is accepted, completed.stdout + completed.stderr


def test_remote_revalidation_rejects_live_repository_identity_mismatch() -> None:
    tag_refs = (
        f"{TEST_TAG_OBJECT_SHA}\trefs/tags/{TEST_RELEASE_TAG}\n"
        f"{TEST_SOURCE_SHA}\trefs/tags/{TEST_RELEASE_TAG}^{{}}\n"
    )

    completed = run_remote_revalidation(
        tag_refs,
        live_repository="substituted/repository",
    )

    assert completed.returncode != 0


@pytest.mark.parametrize(
    "repository_state",
    [
        "",
        "Herbertmt978/grott\t\n",
        "Herbertmt978/grott\tbad branch\n",
        "Herbertmt978/grott\tmaster\textra\n",
        "Herbertmt978/grott\tmaster\nHerbertmt978/grott\tmaster\n",
    ],
)
def test_remote_revalidation_rejects_malformed_live_repository_state(
    repository_state: str,
) -> None:
    tag_refs = (
        f"{TEST_TAG_OBJECT_SHA}\trefs/tags/{TEST_RELEASE_TAG}\n"
        f"{TEST_SOURCE_SHA}\trefs/tags/{TEST_RELEASE_TAG}^{{}}\n"
    )

    completed = run_remote_revalidation(
        tag_refs,
        repository_state=repository_state,
    )

    assert completed.returncode != 0


def test_publish_builds_and_verifies_both_manifests_before_prerelease() -> None:
    workflow = load_workflow("publish-ghcr.yml")
    publish_job = workflow["jobs"]["publish"]
    release_job = workflow["jobs"]["prerelease"]
    builds = build_steps(workflow)
    by_file = {step.get("with", {}).get("file"): step for step in builds}

    assert publish_job["permissions"] == {"contents": "read", "packages": "write"}
    assert set(by_file) == {"docker/dockerfile", "addons/grott/Dockerfile"}
    for step in by_file.values():
        assert step["with"].get("context") == "."
        assert platforms(step) == REQUIRED_PLATFORMS
        assert step["with"].get("push") is True
        assert step["with"].get("sbom") is True
        assert step["with"].get("provenance") == "mode=max"
        assert "latest" not in str(step["with"].get("tags", "")).lower()

    manifest_step = next(
        step
        for step in publish_job["steps"]
        if step.get("name") == "Verify candidate manifests by digest"
    )
    manifest_script = manifest_step["run"]
    assert "RUNTIME_IMAGE" in manifest_script and "ADDON_IMAGE" in manifest_script
    assert "imagetools inspect" in manifest_script
    assert all(platform in manifest_script for platform in REQUIRED_PLATFORMS)

    assert release_job["needs"] == "publish"
    assert release_job["permissions"] == {"contents": "write"}
    assert any(
        "--prerelease" in str(step.get("run", "")) for step in release_job["steps"]
    )


def test_publish_stages_and_verifies_both_digests_before_promoting_final_tags() -> None:
    workflow = load_workflow("publish-ghcr.yml")
    publish_steps = workflow["jobs"]["publish"]["steps"]
    builds = [
        step
        for step in publish_steps
        if str(step.get("uses", "")).startswith("docker/build-push-action@")
    ]
    assert {step.get("id") for step in builds} == {"runtime_build", "addon_build"}
    for step in builds:
        tags = str(step["with"].get("tags", ""))
        assert "candidate-" in tags and "needs.validate.outputs.source_sha" in tags
        assert "steps.release_input.outputs.tag" not in tags
        assert "steps.release_input.outputs.version" not in tags

    verify_index = next(
        index
        for index, step in enumerate(publish_steps)
        if step.get("name") == "Verify candidate manifests by digest"
    )
    promote_index = next(
        index
        for index, step in enumerate(publish_steps)
        if step.get("name") == "Promote verified digests to release tags"
    )
    build_indexes = [publish_steps.index(step) for step in builds]
    assert max(build_indexes) < verify_index < promote_index

    verify_step = publish_steps[verify_index]
    assert "steps.runtime_build.outputs.digest" in verify_step["env"]["RUNTIME_DIGEST"]
    assert "steps.addon_build.outputs.digest" in verify_step["env"]["ADDON_DIGEST"]
    assert "${RUNTIME_IMAGE}@${RUNTIME_DIGEST}" in verify_step["run"]
    assert "${ADDON_IMAGE}@${ADDON_DIGEST}" in verify_step["run"]

    promote_step = publish_steps[promote_index]
    assert "imagetools create" in promote_step["run"]
    assert "${RUNTIME_IMAGE}@${RUNTIME_DIGEST}" in promote_step["run"]
    assert "${ADDON_IMAGE}@${ADDON_DIGEST}" in promote_step["run"]
    assert (
        "RELEASE_TAG" in promote_step["env"]
        and "RELEASE_VERSION" in promote_step["env"]
    )


def test_publish_validates_and_scans_exact_candidate_platform_digests_before_guard() -> (
    None
):
    publish_steps = load_workflow("publish-ghcr.yml")["jobs"]["publish"]["steps"]
    manifest_index = next(
        index
        for index, step in enumerate(publish_steps)
        if step.get("name") == "Verify candidate manifests by digest"
    )
    gate_index = next(
        index
        for index, step in enumerate(publish_steps)
        if step.get("name") == "Validate and scan every staged candidate platform"
    )
    guard_index = next(
        index
        for index, step in enumerate(publish_steps)
        if step.get("name") == "Guard final tags against conflicting digests"
    )
    promotion_index = next(
        index
        for index, step in enumerate(publish_steps)
        if step.get("name") == "Promote verified digests to release tags"
    )
    assert manifest_index < gate_index < guard_index < promotion_index

    gate = publish_steps[gate_index]
    script = gate["run"]
    assert gate.get("continue-on-error") is not True
    assert "if" not in gate
    assert gate["env"] == {
        "RUNTIME_DIGEST": "${{ steps.runtime_build.outputs.digest }}",
        "ADDON_DIGEST": "${{ steps.addon_build.outputs.digest }}",
    }
    assert all(platform in script for platform in REQUIRED_PLATFORMS)
    assert 'validate_and_scan "${RUNTIME_IMAGE}" "${RUNTIME_DIGEST}"' in script
    assert 'validate_and_scan "${ADDON_IMAGE}" "${ADDON_DIGEST}"' in script
    assert 'manifest_ref="${image}@${manifest_digest}"' in script
    assert 'platform_ref="${image}@${platform_digest}"' in script
    assert "docker buildx imagetools inspect" in script and "--raw" in script
    assert "jq -er" in script
    for token in (
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
        '"${platform_ref}"',
    ):
        assert token in script


def test_validator_rejects_buildx_initialized_before_qemu(tmp_path: Path) -> None:
    copy_release_inputs(tmp_path)
    ci_path = tmp_path / ".github" / "workflows" / "ci.yml"
    workflow = validate_release.load_mapping(ci_path)
    job_steps = workflow["jobs"]["test"]["steps"]
    qemu_index = next(
        index
        for index, step in enumerate(job_steps)
        if str(step.get("uses", "")).startswith("docker/setup-qemu-action@")
    )
    qemu_step = job_steps.pop(qemu_index)
    buildx_index = next(
        index
        for index, step in enumerate(job_steps)
        if str(step.get("uses", "")).startswith("docker/setup-buildx-action@")
    )
    job_steps.insert(buildx_index + 1, qemu_step)
    ci_path.write_text(yaml.safe_dump(workflow, sort_keys=False), encoding="utf-8")

    result = run_validator("--check-worktree", root=tmp_path)

    assert result.returncode == 1
    assert "QEMU before Buildx" in result.stderr


def test_final_ref_conflicts_are_guarded_before_promotion_and_verified_afterward() -> (
    None
):
    publish_steps = load_workflow("publish-ghcr.yml")["jobs"]["publish"]["steps"]
    guard_index = next(
        index
        for index, step in enumerate(publish_steps)
        if step.get("name") == "Guard final tags against conflicting digests"
    )
    promotion_index = next(
        index
        for index, step in enumerate(publish_steps)
        if step.get("name") == "Promote verified digests to release tags"
    )
    verification_index = next(
        index
        for index, step in enumerate(publish_steps)
        if step.get("name") == "Verify promoted release tags"
    )
    assert guard_index < promotion_index < verification_index

    guard = publish_steps[guard_index]["run"]
    assert 'ERROR: ${ref}: not found' in guard
    assert "manifest unknown|not found|no such manifest" not in guard
    assert "already resolves to expected digest" in guard
    assert "ref exists with conflicting digest" in guard
    assert all(
        token in guard
        for token in ("RUNTIME_IMAGE", "ADDON_IMAGE", "RELEASE_TAG", "RELEASE_VERSION")
    )

    verification = publish_steps[verification_index]["run"]
    assert "post-promotion ref does not resolve to expected digest" in verification
    assert all(
        token in verification
        for token in ("RUNTIME_IMAGE", "ADDON_IMAGE", "RELEASE_TAG", "RELEASE_VERSION")
    )


@pytest.mark.parametrize(
    ("mode", "accepted"),
    (
        ("exact-absence", True),
        ("equal", True),
        ("conflict", False),
        ("ambiguous-not-found", False),
        ("authentication", False),
        ("timeout", False),
        ("malformed", False),
    ),
)
def test_final_ref_guard_accepts_only_exact_absence_or_equal_digest(
    mode: str, accepted: bool
) -> None:
    completed = run_final_ref_guard(mode)

    assert (completed.returncode == 0) is accepted, completed.stdout + completed.stderr


def test_final_ref_digest_lookups_use_json_manifest_and_validate_sha256() -> None:
    publish_steps = load_workflow("publish-ghcr.yml")["jobs"]["publish"]["steps"]
    for step_name in (
        "Guard final tags against conflicting digests",
        "Verify promoted release tags",
    ):
        script = next(
            step["run"] for step in publish_steps if step.get("name") == step_name
        )
        assert "{{.Manifest.Digest}}" not in script
        assert "--format '{{json .Manifest}}'" in script
        assert "jq -er '.digest'" in script
        assert "^sha256:[0-9a-f]{64}$" in script


def test_remote_tag_is_revalidated_against_source_sha_at_all_three_release_gates() -> (
    None
):
    workflow = load_workflow("publish-ghcr.yml")
    publish_job = workflow["jobs"]["publish"]
    prerelease_job = workflow["jobs"]["prerelease"]
    publish_steps = publish_job["steps"]
    prerelease_steps = prerelease_job["steps"]

    login_index = next(
        index
        for index, step in enumerate(publish_steps)
        if str(step.get("uses", "")).startswith("docker/login-action@")
    )
    promotion_index = next(
        index
        for index, step in enumerate(publish_steps)
        if step.get("name") == "Promote verified digests to release tags"
    )
    before_login = publish_steps[login_index - 1]
    before_promotion = publish_steps[promotion_index - 1]
    before_prerelease = prerelease_steps[-2]
    assert before_login["name"] == "Revalidate remote tag before package login"
    assert before_promotion["name"] == "Revalidate remote tag before final promotion"
    assert before_prerelease["name"] == "Revalidate remote tag before prerelease"

    for step in (before_login, before_promotion, before_prerelease):
        script = step["run"]
        assert "gh api" in script
        assert '"repos/${GITHUB_REPOSITORY}"' in script
        assert "repository_state=" in script
        assert "live_default_branch=" in script
        assert 'repository_state_after="$(get_repository_state)"' in script
        assert '[[ "${repository_state_after}" != "${repository_state}" ]]' in script
        assert "git ls-remote" in script
        assert 'branch_ref="refs/heads/${live_default_branch}"' in script
        assert (
            '[[ "${branch_count}" -ne 1 || "${branch_unexpected}" == "true" '
            '|| "${branch_sha}" != "${SOURCE_SHA}" ]]' in script
        )
        assert "refs/tags/${RELEASE_TAG}^{}" in script
        assert '[[ "${direct_count}" -ne 1' in script
        assert '"${peeled_count}" -ne 1' in script
        assert '[[ "${direct_sha}" == "${peeled_sha}" ]]' in script
        assert '[[ "${peeled_sha}" != "${SOURCE_SHA}" ]]' in script
        assert "else print direct_sha" not in script
        assert "SOURCE_SHA" in step["env"]
        assert "RELEASE_TAG" in step["env"]
        assert step["env"]["GH_TOKEN"] == "${{ github.token }}"
        assert step["env"]["VALIDATED_DEFAULT_BRANCH"] == (
            "${{ github.event.repository.default_branch }}"
        )
        assert "DEFAULT_BRANCH" not in step["env"]
        assert (
            '"${live_default_branch}" != "${VALIDATED_DEFAULT_BRANCH}"' in script
        )

    assert "needs.validate.outputs.source_sha" in before_login["env"]["SOURCE_SHA"]
    assert "needs.validate.outputs.source_sha" in before_promotion["env"]["SOURCE_SHA"]
    assert "needs.publish.outputs.source_sha" in before_prerelease["env"]["SOURCE_SHA"]

    create_release = prerelease_steps[-1]
    assert "needs.publish.outputs.source_sha" in create_release["env"]["SOURCE_SHA"]
    assert '--target "${SOURCE_SHA}"' in create_release["run"]
    assert (
        publish_job["outputs"]["source_sha"]
        == "${{ needs.validate.outputs.source_sha }}"
    )


@pytest.mark.parametrize(
    ("tag", "accepted"),
    [
        ("v1.2.3-1abc", True),
        ("v1.2.3-alpha.1", True),
        ("v0.0.0", True),
        ("v1.2.3-01", False),
        ("v1.2.3-alpha..1", False),
        ("v01.2.3", False),
    ],
)
def test_python_and_workflow_shell_tag_grammars_agree(tag: str, accepted: bool) -> None:
    assert validate_release.validate_tag_syntax(tag) is accepted
    assert verify_release_controls.valid_release_tag(tag) is accepted
    assert workflow_shell_accepts_tag(tag) is accepted


def test_static_validator_accepts_dirty_development_tree_without_worktree_gate() -> None:
    version = yaml.safe_load(
        (ROOT / "addons" / "grott" / "config.yaml").read_text(encoding="utf-8")
    )["version"]
    result = run_validator("--tag", f"v{version}")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("state", "accepted", "expected_error"),
    [
        pytest.param("clean", True, "", id="clean-git-worktree"),
        pytest.param(
            "modified", False, "worktree must be clean", id="modified-tracked-file"
        ),
        pytest.param(
            "untracked", False, "worktree must be clean", id="untracked-file"
        ),
        pytest.param(
            "nonrepo", False, "not a Git worktree", id="not-a-git-worktree"
        ),
    ],
)
def test_check_worktree_requires_clean_git_repository(
    tmp_path: Path, state: str, accepted: bool, expected_error: str
) -> None:
    if state == "nonrepo":
        copy_release_inputs(tmp_path)
    else:
        initialize_clean_release_worktree(tmp_path)
    if state == "modified":
        readme = tmp_path / "README.md"
        readme.write_text(
            readme.read_text(encoding="utf-8") + "\nLocal release edit.\n",
            encoding="utf-8",
        )
    elif state == "untracked":
        (tmp_path / "release-scratch.txt").write_text("untracked\n", encoding="utf-8")

    result = run_validator("--check-worktree", root=tmp_path)

    assert (result.returncode == 0) is accepted, result.stdout + result.stderr
    if expected_error:
        assert expected_error in result.stderr


def test_release_runbook_explains_clean_gate_and_annotated_remote_refs() -> None:
    runbook = (ROOT / "RELEASING.md").read_text(encoding="utf-8")

    assert "git status --porcelain --untracked-files=all" in runbook
    assert "direct remote ref is the annotated tag-object SHA" in runbook
    assert "peeled remote ref is the release commit SHA" in runbook
    assert "must differ" in runbook
    assert "protected workflow-dispatch commit" in runbook
    assert 'RELEASE_REPO="Herbertmt978/grott"' in runbook
    assert '--repo "${RELEASE_REPO}"' in runbook
    assert 'git push "${RELEASE_REMOTE}"' in runbook
    assert "bypass actors" in runbook
    assert "administrators are not allowed to bypass" in runbook


@pytest.mark.parametrize(
    "tag",
    [
        # Deliberately missing the required v prefix; not a version authority.
        "0.1.10-beta",
        "v01.1.0",
        "v1.01.0",
        "v1.0.01",
        "v1.0",
        "v1.0.0-",
        "v1.0.0-beta..1",
        "v1.0.0+build",
        "v1.0.0-01",
        "v1.0.0;echo-owned",
    ],
)
def test_validator_rejects_noncanonical_release_tags(tag: str) -> None:
    result = run_validator("--check-worktree", "--tag", tag)
    assert result.returncode == 2
    assert "valid release tag" in result.stderr


def test_validator_rejects_tag_that_disagrees_with_addon_version() -> None:
    result = run_validator("--check-worktree", "--tag", "v9.9.9-beta")
    assert result.returncode == 1
    assert "add-on version" in result.stderr
    assert "v9.9.9-beta" in result.stderr


def test_validator_reports_mutable_action_in_copied_worktree(tmp_path: Path) -> None:
    copy_release_inputs(tmp_path)
    ci_path = tmp_path / ".github" / "workflows" / "ci.yml"
    ci = ci_path.read_text(encoding="utf-8")
    ci = re.sub(r"actions/checkout@[0-9a-f]{40}", "actions/checkout@v6", ci, count=1)
    ci_path.write_text(ci, encoding="utf-8")

    result = run_validator("--check-worktree", root=tmp_path)
    assert result.returncode == 1
    assert "mutable action reference" in result.stderr
    assert "actions/checkout@v6" in result.stderr


def test_validator_rejects_direct_input_expression_with_compact_whitespace(
    tmp_path: Path,
) -> None:
    copy_release_inputs(tmp_path)
    publish_path = tmp_path / ".github" / "workflows" / "publish-ghcr.yml"
    publish = publish_path.read_text(encoding="utf-8")
    publish = publish.replace(
        "          set -euo pipefail\n",
        '          set -euo pipefail\n          echo "${{inputs.tag}}"\n',
        1,
    )
    publish_path.write_text(publish, encoding="utf-8")

    result = run_validator("--check-worktree", root=tmp_path)
    assert result.returncode == 1
    assert "interpolates inputs.tag directly" in result.stderr


def test_validator_rejects_mutable_reusable_workflow_reference(tmp_path: Path) -> None:
    copy_release_inputs(tmp_path)
    ci_path = tmp_path / ".github" / "workflows" / "ci.yml"
    ci = ci_path.read_text(encoding="utf-8")
    ci += "\n  delegated:\n    uses: example/example/.github/workflows/check.yml@v1\n"
    ci_path.write_text(ci, encoding="utf-8")

    result = run_validator("--check-worktree", root=tmp_path)
    assert result.returncode == 1
    assert "mutable action reference" in result.stderr
    assert "check.yml@v1" in result.stderr


def test_validator_rejects_build_that_pushes_final_release_tag_directly(
    tmp_path: Path,
) -> None:
    copy_release_inputs(tmp_path)
    publish_path = tmp_path / ".github" / "workflows" / "publish-ghcr.yml"
    workflow = validate_release.load_mapping(publish_path)
    build = next(
        step
        for step in workflow["jobs"]["publish"]["steps"]
        if str(step.get("uses", "")).startswith("docker/build-push-action@")
    )
    build["with"]["tags"] = (
        "${{ env.RUNTIME_IMAGE }}:${{ steps.release_input.outputs.tag }}"
    )
    publish_path.write_text(yaml.safe_dump(workflow, sort_keys=False), encoding="utf-8")

    result = run_validator("--check-worktree", root=tmp_path)
    assert result.returncode == 1
    assert "must stage a source-SHA candidate tag" in result.stderr


def test_validator_rejects_promotion_before_digest_verification(tmp_path: Path) -> None:
    copy_release_inputs(tmp_path)
    publish_path = tmp_path / ".github" / "workflows" / "publish-ghcr.yml"
    workflow = validate_release.load_mapping(publish_path)
    publish_steps = workflow["jobs"]["publish"]["steps"]
    verification_index = next(
        (
            index
            for index, step in enumerate(publish_steps)
            if step.get("name")
            in {"Verify candidate manifests by digest", "Verify published manifests"}
        ),
        len(publish_steps),
    )
    publish_steps.insert(
        verification_index,
        {
            "name": "Promote verified digests to release tags",
            "run": "docker buildx imagetools create --tag final image@digest",
        },
    )
    publish_path.write_text(yaml.safe_dump(workflow, sort_keys=False), encoding="utf-8")

    result = run_validator("--check-worktree", root=tmp_path)
    assert result.returncode == 1
    assert "safe order" in result.stderr


@pytest.mark.parametrize(
    ("mutation_name", "expected_error"),
    [
        ("push_trigger", "workflow_dispatch-only"),
        ("no_concurrency", "concurrency"),
        ("gate_permissions", "release-control gate permissions"),
        ("gate_dependency", "release-control gate must depend on validate"),
        ("gate_checkout_ref", "release-control gate checkout"),
        ("gate_verifier_noop", "release-control gate must hash-check"),
        ("gate_extra_step", "release-control gate must use only"),
        ("gate_environment", "only publish and prerelease jobs"),
        ("publish_dependency", "publish job must depend on validate and the release-control gate"),
        ("publish_permissions", "publish job permissions"),
        ("prerelease_dependency", "prerelease job must depend on publish"),
        ("release_environment", "release environment"),
        ("package_checkout_ref", "validated source SHA"),
        ("dispatch_comparison_removed", "dispatch SHA exact equality"),
        ("dispatch_comparison_substituted", "dispatch SHA exact equality"),
        ("dispatch_comparison_reversed", "dispatch SHA exact equality"),
        ("dispatch_comparison_weakened", "dispatch SHA exact equality"),
        ("source_lightweight_tag_allowed", "annotated tag object"),
        ("remote_live_api_removed", "live repository identity"),
        ("remote_live_identity_allowed", "live repository identity"),
        ("remote_event_default_reused", "live repository identity"),
        ("remote_live_branch_name_change_allowed", "live repository identity"),
        ("remote_live_state_race_allowed", "live repository identity"),
        ("remote_branch_duplicate_allowed", "protected default branch"),
        ("remote_branch_wrong_comparison", "protected default branch"),
        ("remote_duplicate_allowed", "exactly one direct tag-object ref"),
        ("remote_missing_peeled_fallback", "exactly one direct tag-object ref"),
        ("remote_direct_equals_peeled_allowed", "distinct tag-object SHA"),
        ("remote_wrong_peeled_comparison", "peeled SHA must equal"),
        ("remote_revalidation", "remote tag revalidation"),
        ("prerelease_remote_removed", "prerelease job must use only"),
        ("prerelease_remote_reordered", "prerelease job must use only"),
        ("build_push", "push: true"),
        ("build_sbom", "sbom: true"),
        ("build_provenance", "provenance: mode=max"),
        ("digest_verification", "digest verification"),
        ("candidate_validation", "candidate platform validation"),
        ("candidate_missing_platform", "candidate platform validation"),
        ("candidate_mutable_ref", "candidate platform validation"),
        ("candidate_mutable_scanner", "candidate platform validation"),
        ("candidate_no_artifact_validator", "candidate platform validation"),
        ("candidate_no_local_scan", "candidate platform validation"),
        ("candidate_after_guard", "candidate platform validation"),
        ("conflict_guard", "conflict guard"),
        ("digest_promotion", "digest-bound promotion"),
        ("post_promotion", "post-promotion verification"),
        ("extra_privileged_step", "reviewed publish step sequence"),
        ("broken_digest_lookup", "verified JSON manifest digest extraction"),
        ("extra_packages_job", "exactly validate, gate, publish, and prerelease"),
        ("workflow_runtime_image", "canonical publish workflow hash"),
        ("workflow_addon_image", "canonical publish workflow hash"),
        ("workflow_concurrency_run_id", "canonical publish workflow hash"),
        ("workflow_always_guard", "canonical publish workflow hash"),
        ("workflow_build_secrets", "canonical publish workflow hash"),
        ("dispatch_versioned_description", "tag description must be version-neutral"),
        ("publish_runtime_digest_output", "verified runtime and add-on digest outputs"),
        ("publish_addon_digest_output", "verified runtime and add-on digest outputs"),
        ("prerelease_runtime_digest_env", "prerelease digest inputs"),
        ("prerelease_addon_digest_env", "prerelease digest inputs"),
        ("prerelease_weak_digest_pattern", "fail closed on invalid digest syntax"),
        ("prerelease_digest_not_failclosed", "fail closed on invalid digest syntax"),
        ("prerelease_missing_immutable_ref", "immutable image refs"),
        ("prerelease_missing_platform", "every supported platform"),
        ("prerelease_curated_wrong_path", "canonical curated release notes path"),
        ("prerelease_curated_wrong_ref", "verified source SHA"),
        (
            "prerelease_curated_fetch_not_failclosed",
            "fail closed when the curated release notes fetch fails",
        ),
        (
            "prerelease_curated_blank_not_failclosed",
            "curated release notes are missing or blank",
        ),
        (
            "prerelease_missing_body_composition",
            "combine immutable image metadata and curated release notes",
        ),
        ("prerelease_no_notes", "publish the exact composed body with --notes-file"),
        ("prerelease_generate_notes", "must not use generated release notes"),
        ("prerelease_missing_existing_guard", "exact existing-release guard"),
        ("prerelease_loose_existing_tag", "matching release tag"),
        ("prerelease_loose_existing_state", "non-draft prerelease state"),
        ("prerelease_loose_existing_body", "exact full release body"),
        ("prerelease_swallow_lookup_errors", "explicit absence from lookup failure"),
        ("prerelease_no_post_verify", "post-verify the final prerelease"),
        ("prerelease_no_retry_recovery", "idempotent retry and race recovery"),
        ("noop_remote_login", "canonical publish workflow hash"),
        ("noop_candidate_verify", "canonical publish workflow hash"),
        ("noop_conflict_guard", "canonical publish workflow hash"),
        ("noop_promotion", "canonical publish workflow hash"),
        ("noop_postverify", "canonical publish workflow hash"),
        ("noop_prerelease_remote", "canonical publish workflow hash"),
        ("noop_release_create", "canonical publish workflow hash"),
        ("metadata_continue_on_error", "canonical publish workflow hash"),
        ("metadata_false_if", "canonical publish workflow hash"),
        ("metadata_release_tag_env", "canonical publish workflow hash"),
        ("metadata_guard_digest_env", "canonical publish workflow hash"),
        ("metadata_candidate_digest_env", "candidate platform validation"),
        ("metadata_candidate_continue", "candidate platform validation"),
        ("metadata_candidate_false_if", "candidate platform validation"),
        ("metadata_promotion_digest_env", "canonical publish workflow hash"),
        ("metadata_postverify_digest_env", "canonical publish workflow hash"),
    ],
)
def test_canonical_validator_kills_release_policy_mutations(
    tmp_path: Path, mutation_name: str, expected_error: str
) -> None:
    def mutate(workflow: dict) -> None:
        jobs = workflow["jobs"]
        publish_job = jobs["publish"]
        publish_steps = publish_job["steps"]
        if mutation_name == "push_trigger":
            workflow_trigger(workflow)["push"] = {"tags": ["v*"]}
        elif mutation_name == "no_concurrency":
            workflow.pop("concurrency", None)
        elif mutation_name == "gate_permissions":
            jobs["gate"]["permissions"] = {"contents": "read"}
        elif mutation_name == "gate_dependency":
            jobs["gate"].pop("needs", None)
        elif mutation_name == "gate_checkout_ref":
            checkout = next(
                step
                for step in jobs["gate"]["steps"]
                if str(step.get("uses", "")).startswith("actions/checkout@")
            )
            checkout["with"]["ref"] = "${{ needs.validate.outputs.tag }}"
        elif mutation_name == "gate_verifier_noop":
            step = next(
                step
                for step in jobs["gate"]["steps"]
                if step.get("name") == "Verify public release controls"
            )
            step["run"] = "true"
        elif mutation_name == "gate_extra_step":
            jobs["gate"]["steps"].append({"name": "Extra gate step", "run": "true"})
        elif mutation_name == "gate_environment":
            jobs["gate"]["environment"] = "release"
        elif mutation_name == "publish_dependency":
            publish_job["needs"] = "validate"
        elif mutation_name == "publish_permissions":
            publish_job["permissions"] = {"contents": "read"}
        elif mutation_name == "prerelease_dependency":
            jobs["prerelease"].pop("needs", None)
        elif mutation_name == "release_environment":
            publish_job.pop("environment", None)
        elif mutation_name == "package_checkout_ref":
            checkout = next(
                step
                for step in publish_steps
                if str(step.get("uses", "")).startswith("actions/checkout@")
            )
            checkout.setdefault("with", {})["ref"] = "${{ needs.validate.outputs.tag }}"
        elif mutation_name.startswith("dispatch_comparison_"):
            source_step = next(
                step
                for step in jobs["validate"]["steps"]
                if step.get("name") == "Verify checked-out tag and source commit"
            )
            exact_comparison = '[[ "${tag_sha}" != "${DISPATCH_SHA}" ]]'
            replacement = {
                "dispatch_comparison_removed": "false",
                "dispatch_comparison_substituted": (
                    '[[ "${tag_sha}" != "${head_sha}" ]]'
                ),
                "dispatch_comparison_reversed": (
                    '[[ "${tag_sha}" == "${DISPATCH_SHA}" ]]'
                ),
                "dispatch_comparison_weakened": (
                    '! git merge-base --is-ancestor "${tag_sha}" "${DISPATCH_SHA}"'
                ),
            }[mutation_name]
            source_step["run"] = source_step["run"].replace(
                exact_comparison, replacement, 1
            )
        elif mutation_name == "source_lightweight_tag_allowed":
            source_step = next(
                step
                for step in jobs["validate"]["steps"]
                if step.get("name") == "Verify checked-out tag and source commit"
            )
            source_step["run"] = source_step["run"].replace(
                '[[ "${tag_type}" != "tag" ]]', "false", 1
            )
        elif mutation_name.startswith("remote_") and mutation_name != "remote_revalidation":
            remote_step = next(
                step
                for step in publish_steps
                if step.get("name") == "Revalidate remote tag before package login"
            )
            if mutation_name == "remote_duplicate_allowed":
                remote_step["run"] = remote_step["run"].replace(
                    '"${direct_count}" -ne 1', '"${direct_count}" -lt 1', 1
                )
            elif mutation_name == "remote_live_api_removed":
                remote_step["run"] = remote_step["run"].replace(
                    "gh api", "printf stale", 1
                )
            elif mutation_name == "remote_live_identity_allowed":
                remote_step["run"] = remote_step["run"].replace(
                    '"${live_repository,,}" != "${GITHUB_REPOSITORY,,}"',
                    "false",
                    1,
                )
            elif mutation_name == "remote_event_default_reused":
                remote_step["env"]["DEFAULT_BRANCH"] = (
                    "${{ github.event.repository.default_branch }}"
                )
            elif mutation_name == "remote_live_branch_name_change_allowed":
                remote_step["run"] = remote_step["run"].replace(
                    '"${live_default_branch}" != "${VALIDATED_DEFAULT_BRANCH}"',
                    "false",
                    1,
                )
            elif mutation_name == "remote_live_state_race_allowed":
                remote_step["run"] = remote_step["run"].replace(
                    '[[ "${repository_state_after}" != "${repository_state}" ]]',
                    "false",
                    1,
                )
            elif mutation_name == "remote_branch_duplicate_allowed":
                remote_step["run"] = remote_step["run"].replace(
                    '"${branch_count}" -ne 1', '"${branch_count}" -lt 1', 1
                )
            elif mutation_name == "remote_branch_wrong_comparison":
                remote_step["run"] = remote_step["run"].replace(
                    '"${branch_sha}" != "${SOURCE_SHA}"',
                    '"${branch_sha}" != "${peeled_sha}"',
                    1,
                )
            elif mutation_name == "remote_missing_peeled_fallback":
                remote_step["run"] = remote_step["run"].replace(
                    ' || "${peeled_count}" -ne 1', "", 1
                ).replace(
                    '"${peeled_sha}" != "${SOURCE_SHA}"',
                    '"${peeled_sha:-${direct_sha}}" != "${SOURCE_SHA}"',
                    1,
                )
            elif mutation_name == "remote_direct_equals_peeled_allowed":
                remote_step["run"] = remote_step["run"].replace(
                    '[[ "${direct_sha}" == "${peeled_sha}" ]]', "false", 1
                )
            elif mutation_name == "remote_wrong_peeled_comparison":
                remote_step["run"] = remote_step["run"].replace(
                    '[[ "${peeled_sha}" != "${SOURCE_SHA}" ]]',
                    '[[ "${direct_sha}" != "${SOURCE_SHA}" ]]',
                    1,
                )
        elif mutation_name == "remote_revalidation":
            for job in jobs.values():
                job["steps"] = [
                    step
                    for step in job.get("steps", [])
                    if "Revalidate remote tag" not in step.get("name", "")
                ]
        elif mutation_name == "prerelease_remote_removed":
            prerelease_steps = jobs["prerelease"]["steps"]
            jobs["prerelease"]["steps"] = [
                step
                for step in prerelease_steps
                if step.get("name") != "Revalidate remote tag before prerelease"
            ]
        elif mutation_name == "prerelease_remote_reordered":
            jobs["prerelease"]["steps"].reverse()
        elif mutation_name in {"build_push", "build_sbom", "build_provenance"}:
            build = next(
                step
                for step in publish_steps
                if str(step.get("uses", "")).startswith("docker/build-push-action@")
            )
            option = mutation_name.removeprefix("build_")
            build["with"][option] = False
        elif mutation_name == "digest_verification":
            step = next(
                step
                for step in publish_steps
                if step.get("name") == "Verify candidate manifests by digest"
            )
            step["run"] = "true"
        elif mutation_name in {
            "candidate_validation",
            "candidate_missing_platform",
            "candidate_mutable_ref",
            "candidate_mutable_scanner",
            "candidate_no_artifact_validator",
            "candidate_no_local_scan",
        }:
            step = next(
                step
                for step in publish_steps
                if step.get("name")
                == "Validate and scan every staged candidate platform"
            )
            if mutation_name == "candidate_validation":
                step["run"] = "true"
            elif mutation_name == "candidate_missing_platform":
                step["run"] = step["run"].replace(" linux/386", "", 1)
            elif mutation_name == "candidate_mutable_ref":
                step["run"] = step["run"].replace(
                    'platform_ref="${image}@${platform_digest}"',
                    'platform_ref="${image}:candidate-${SOURCE_SHA}"',
                )
            elif mutation_name == "candidate_mutable_scanner":
                step["run"] = step["run"].replace(
                    PINNED_TRIVY_IMAGE,
                    "aquasec/trivy:latest",
                )
            elif mutation_name == "candidate_no_artifact_validator":
                step["run"] = step["run"].replace(
                    "/usr/local/bin/validate_container_artifact.py",
                    "/bin/true",
                )
            else:
                step["run"] = step["run"].replace("--image-src docker", "")
        elif mutation_name == "candidate_after_guard":
            gate_index = next(
                index
                for index, step in enumerate(publish_steps)
                if step.get("name")
                == "Validate and scan every staged candidate platform"
            )
            gate = publish_steps.pop(gate_index)
            guard_index = next(
                index
                for index, step in enumerate(publish_steps)
                if step.get("name") == "Guard final tags against conflicting digests"
            )
            publish_steps.insert(guard_index + 1, gate)
        elif mutation_name == "conflict_guard":
            step = next(
                (
                    step
                    for step in publish_steps
                    if step.get("name")
                    == "Guard final tags against conflicting digests"
                ),
                None,
            )
            if step is None:
                promotion = next(
                    index
                    for index, item in enumerate(publish_steps)
                    if item.get("name") == "Promote verified digests to release tags"
                )
                publish_steps.insert(
                    promotion,
                    {
                        "name": "Guard final tags against conflicting digests",
                        "run": "true",
                    },
                )
            else:
                step["run"] = "true"
        elif mutation_name == "digest_promotion":
            step = next(
                step
                for step in publish_steps
                if step.get("name") == "Promote verified digests to release tags"
            )
            step["run"] = "echo promoted"
        elif mutation_name == "post_promotion":
            step = next(
                (
                    step
                    for step in publish_steps
                    if step.get("name") == "Verify promoted release tags"
                ),
                None,
            )
            if step is None:
                publish_steps.append(
                    {"name": "Verify promoted release tags", "run": "true"}
                )
            else:
                step["run"] = "true"
        elif mutation_name == "extra_privileged_step":
            publish_steps.insert(
                1, {"name": "Run tag-controlled helper", "run": "./helper.sh"}
            )
        elif mutation_name == "broken_digest_lookup":
            for step_name in (
                "Guard final tags against conflicting digests",
                "Verify promoted release tags",
            ):
                step = next(
                    step for step in publish_steps if step.get("name") == step_name
                )
                step["run"] = re.sub(
                    r"--format '\{\{json \.Manifest}}'\s*\|\s*jq -er '\.digest'",
                    "--format '{{.Manifest.Digest}}'",
                    step["run"],
                )
        elif mutation_name == "extra_packages_job":
            jobs["rogue"] = {
                "runs-on": "ubuntu-latest",
                "permissions": {"packages": "write"},
                "steps": [{"name": "Arbitrary package write", "run": "echo unsafe"}],
            }
        elif mutation_name == "workflow_runtime_image":
            workflow["env"]["RUNTIME_IMAGE"] = "ghcr.io/attacker/runtime"
        elif mutation_name == "workflow_addon_image":
            workflow["env"]["ADDON_IMAGE"] = "ghcr.io/attacker/addon"
        elif mutation_name == "workflow_concurrency_run_id":
            workflow["concurrency"]["group"] = "${{ github.run_id }}"
        elif mutation_name == "workflow_always_guard":
            publish_job["if"] = (
                "${{ always() || (github.ref == "
                "format('refs/heads/{0}', github.event.repository.default_branch) "
                "&& github.ref_protected) }}"
            )
        elif mutation_name == "workflow_build_secrets":
            build = next(
                step
                for step in publish_steps
                if str(step.get("uses", "")).startswith("docker/build-push-action@")
            )
            build["with"]["secrets"] = "GITHUB_TOKEN=${{ secrets.GITHUB_TOKEN }}"
        elif mutation_name == "dispatch_versioned_description":
            workflow_trigger(workflow)["workflow_dispatch"]["inputs"]["tag"][
                "description"
            ] = "Publish v9.9.9-beta"
        elif mutation_name in {
            "publish_runtime_digest_output",
            "publish_addon_digest_output",
        }:
            output = {
                "publish_runtime_digest_output": "runtime_digest",
                "publish_addon_digest_output": "addon_digest",
            }[mutation_name]
            publish_job["outputs"][output] = "sha256:" + "0" * 64
        elif mutation_name.startswith("prerelease_"):
            create_step = next(
                step
                for step in jobs["prerelease"]["steps"]
                if step.get("name")
                == PRERELEASE_STEP_NAME
            )
            if mutation_name == "prerelease_runtime_digest_env":
                create_step["env"]["RUNTIME_DIGEST"] = "sha256:" + "0" * 64
            elif mutation_name == "prerelease_addon_digest_env":
                create_step["env"]["ADDON_DIGEST"] = "sha256:" + "1" * 64
            elif mutation_name == "prerelease_weak_digest_pattern":
                create_step["run"] = create_step["run"].replace(
                    "^sha256:[0-9a-f]{64}$", ".*", 1
                )
            elif mutation_name == "prerelease_digest_not_failclosed":
                create_step["run"] = create_step["run"].replace("exit 1", "true")
            elif mutation_name == "prerelease_missing_immutable_ref":
                create_step["run"] = create_step["run"].replace(
                    "${RUNTIME_IMAGE}@${RUNTIME_DIGEST}",
                    "${RUNTIME_IMAGE}:${RELEASE_TAG}",
                    1,
                )
            elif mutation_name == "prerelease_missing_platform":
                create_step["run"] = create_step["run"].replace(
                    ", linux/386", "", 1
                )
            elif mutation_name == "prerelease_curated_wrong_path":
                create_step["run"] = create_step["run"].replace(
                    "docs/releases/${RELEASE_TAG}.md",
                    "docs/drafts/${RELEASE_TAG}.md",
                    1,
                )
            elif mutation_name == "prerelease_curated_wrong_ref":
                create_step["run"] = create_step["run"].replace(
                    '-f ref="${SOURCE_SHA}"', '-f ref="${RELEASE_TAG}"', 1
                )
            elif mutation_name == "prerelease_curated_fetch_not_failclosed":
                create_step["run"] = create_step["run"].replace(
                    "if ! gh api --method GET", "if gh api --method GET", 1
                )
            elif mutation_name == "prerelease_curated_blank_not_failclosed":
                create_step["run"] = create_step["run"].replace(
                    '[[ ! -s "${curated_notes_file}" ]]',
                    '[[ -s "${curated_notes_file}" ]]',
                    1,
                )
            elif mutation_name == "prerelease_missing_body_composition":
                create_step["run"] = create_step["run"].replace(
                    'cat "${curated_notes_file}"', "true", 1
                )
            elif mutation_name == "prerelease_generate_notes":
                create_step["run"] = create_step["run"].replace(
                    '--notes-file "${release_body_file}"',
                    '--generate-notes --notes-file "${release_body_file}"',
                    1,
                )
            elif mutation_name == "prerelease_missing_existing_guard":
                create_step["run"] = create_step["run"].replace(
                    "lookup_release()", "lookup_release_removed()", 1
                )
            elif mutation_name == "prerelease_loose_existing_tag":
                create_step["run"] = create_step["run"].replace(
                    ".tag_name == $tag", "true", 1
                )
            elif mutation_name == "prerelease_loose_existing_state":
                create_step["run"] = create_step["run"].replace(
                    ".draft == false and .prerelease == true", "true", 1
                )
            elif mutation_name == "prerelease_loose_existing_body":
                create_step["run"] = create_step["run"].replace(
                    '(.body // "") == $expected_body', "true", 1
                )
            elif mutation_name == "prerelease_swallow_lookup_errors":
                create_step["run"] = create_step["run"].replace(
                    'return "${LOOKUP_FAILED}"',
                    'return "${LOOKUP_ABSENT}"',
                    1,
                )
            elif mutation_name == "prerelease_no_post_verify":
                create_step["run"] = create_step["run"].replace(
                    'verify_final_release "${create_status}"',
                    "true",
                    1,
                )
            elif mutation_name == "prerelease_no_retry_recovery":
                create_step["run"] = create_step["run"].replace(
                    'create_status=$?', "create_status=0", 1
                )
            else:
                create_step["run"] = create_step["run"].replace(
                    '--notes-file "${release_body_file}"', "", 1
                )
        elif mutation_name.startswith("noop_"):
            target_job = publish_job
            target_name = {
                "noop_remote_login": "Revalidate remote tag before package login",
                "noop_candidate_verify": "Verify candidate manifests by digest",
                "noop_conflict_guard": "Guard final tags against conflicting digests",
                "noop_promotion": "Promote verified digests to release tags",
                "noop_postverify": "Verify promoted release tags",
                "noop_prerelease_remote": "Revalidate remote tag before prerelease",
                    "noop_release_create": PRERELEASE_STEP_NAME,
            }[mutation_name]
            if mutation_name in {"noop_prerelease_remote", "noop_release_create"}:
                target_job = jobs["prerelease"]
            step = next(
                step for step in target_job["steps"] if step.get("name") == target_name
            )
            step["run"] = (
                "\n".join(f"# {line}" for line in step["run"].splitlines()) + "\ntrue\n"
            )
        elif mutation_name.startswith("metadata_"):
            target_name = {
                "metadata_continue_on_error": "Guard final tags against conflicting digests",
                "metadata_false_if": "Promote verified digests to release tags",
                "metadata_release_tag_env": "Guard final tags against conflicting digests",
                "metadata_guard_digest_env": "Guard final tags against conflicting digests",
                "metadata_candidate_digest_env": "Validate and scan every staged candidate platform",
                "metadata_candidate_continue": "Validate and scan every staged candidate platform",
                "metadata_candidate_false_if": "Validate and scan every staged candidate platform",
                "metadata_promotion_digest_env": "Promote verified digests to release tags",
                "metadata_postverify_digest_env": "Verify promoted release tags",
            }[mutation_name]
            step = next(
                step for step in publish_steps if step.get("name") == target_name
            )
            if mutation_name == "metadata_continue_on_error":
                step["continue-on-error"] = True
            elif mutation_name == "metadata_false_if":
                step["if"] = "${{ false }}"
            elif mutation_name == "metadata_release_tag_env":
                step["env"]["RELEASE_TAG"] = "${{ inputs.tag }}"
            elif mutation_name == "metadata_candidate_digest_env":
                step["env"]["RUNTIME_DIGEST"] = "sha256:" + "0" * 64
                step["env"]["ADDON_DIGEST"] = "sha256:" + "1" * 64
            elif mutation_name == "metadata_candidate_continue":
                step["continue-on-error"] = True
            elif mutation_name == "metadata_candidate_false_if":
                step["if"] = "${{ false }}"
            else:
                step["env"]["RUNTIME_DIGEST"] = "sha256:" + "0" * 64
                step["env"]["ADDON_DIGEST"] = "sha256:" + "1" * 64

    mutate_publish_workflow(tmp_path, mutate)
    result = run_validator("--check-worktree", root=tmp_path)
    assert result.returncode == 1
    assert expected_error in result.stderr


def test_validator_rejects_duplicate_yaml_key_when_canonical_value_is_last(
    tmp_path: Path,
) -> None:
    copy_release_inputs(tmp_path)
    publish_path = tmp_path / ".github" / "workflows" / "publish-ghcr.yml"
    text = publish_path.read_text(encoding="utf-8")
    step_offset = text.index("      - name: Revalidate remote tag before package login")
    run_offset = text.index("        run: |\n", step_offset)
    text = text[:run_offset] + "        run: echo malicious\n" + text[run_offset:]
    publish_path.write_text(text, encoding="utf-8")

    result = run_validator("--check-worktree", root=tmp_path)

    assert result.returncode == 1
    assert "duplicate mapping key 'run'" in result.stderr


def test_publish_workflow_hash_is_stable_for_mapping_order_and_run_line_endings() -> (
    None
):
    workflow = validate_release.load_mapping(WORKFLOWS / "publish-ghcr.yml")
    expected = validate_release.CANONICAL_PUBLISH_WORKFLOW_SHA256
    assert validate_release.normalized_publish_workflow_sha256(workflow) == expected

    reordered = dict(reversed(list(workflow.items())))
    crlf_workflow = copy.deepcopy(reordered)
    for _, step in steps(crlf_workflow):
        run = step.get("run")
        if isinstance(run, str):
            step["run"] = run.replace("\n", "\r\n")

    assert (
        validate_release.normalized_publish_workflow_sha256(crlf_workflow) == expected
    )
