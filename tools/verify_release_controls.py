"""Verify that GitHub release controls protect an exact release source commit.

The publishing workflow captures GitHub API responses before any package-write
permission is granted.  This tool validates those responses using only the
Python standard library so malformed, missing, or incomplete evidence fails
closed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any


TAG_RE = re.compile(
    r"^v(0|[1-9][0-9]*)\."
    r"(0|[1-9][0-9]*)\."
    r"(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class ReleaseControlError(ValueError):
    """Raised when release-control evidence is missing or insufficient."""


def fail(message: str) -> None:
    raise ReleaseControlError(message)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        fail(f"{path.name} is missing or invalid JSON ({type(exc).__name__})")


def flatten_pages(payload: Any, name: str) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        fail(f"{name} is not an array")
    pages = payload if all(isinstance(page, list) for page in payload) else [payload]
    records: list[dict[str, Any]] = []
    for page in pages:
        for record in page:
            if not isinstance(record, dict):
                fail(f"{name} contains a non-object")
            records.append(record)
    return records


def tag_ruleset_ids(path: Path) -> list[int]:
    records = flatten_pages(load_json(path), "tag ruleset list")
    identifiers: set[int] = set()
    for ruleset in records:
        identifier = ruleset.get("id")
        if type(identifier) is not int or identifier <= 0:
            fail("tag ruleset list contains an invalid id")
        identifiers.add(identifier)
    return sorted(identifiers)


def valid_release_tag(tag: str) -> bool:
    match = TAG_RE.fullmatch(tag)
    if match is None:
        return False
    prerelease = match.group(4)
    if prerelease is None:
        return True
    return all(
        not (
            identifier.isdigit()
            and len(identifier) > 1
            and identifier.startswith("0")
        )
        for identifier in prerelease.split(".")
    )


def has_required_solo_pull_request_rule(rules: list[dict[str, Any]]) -> bool:
    found_pull_request_rule = False
    for rule in rules:
        if rule.get("type") != "pull_request":
            continue
        found_pull_request_rule = True
        parameters = rule.get("parameters")
        if not isinstance(parameters, dict):
            return False
        review_count = parameters.get("required_approving_review_count")
        if not (
            type(review_count) is int
            and review_count == 0
            and parameters.get("dismiss_stale_reviews_on_push") is False
            and parameters.get("require_last_push_approval") is False
            and parameters.get("required_review_thread_resolution") is True
        ):
            return False
    return found_pull_request_rule


def has_required_status_rule(rules: list[dict[str, Any]]) -> bool:
    for rule in rules:
        if rule.get("type") != "required_status_checks":
            continue
        parameters = rule.get("parameters")
        if not isinstance(parameters, dict):
            continue
        checks = parameters.get("required_status_checks")
        if not isinstance(checks, list):
            continue
        contexts = {
            check.get("context")
            for check in checks
            if isinstance(check, dict)
            and isinstance(check.get("context"), str)
        }
        if (
            parameters.get("strict_required_status_checks_policy") is True
            and "test" in contexts
        ):
            return True
    return False


def tag_ruleset_protects_release(detail: Any, identifier: int) -> bool:
    if not isinstance(detail, dict) or detail.get("id") != identifier:
        return False
    conditions = detail.get("conditions")
    ref_name = conditions.get("ref_name") if isinstance(conditions, dict) else None
    includes = ref_name.get("include") if isinstance(ref_name, dict) else None
    excludes = ref_name.get("exclude") if isinstance(ref_name, dict) else None
    rules = detail.get("rules")
    if not isinstance(rules, list):
        return False
    rule_types = {
        rule.get("type") for rule in rules if isinstance(rule, dict)
    }
    include_matches = isinstance(includes, list) and (
        "~ALL" in includes
        or "refs/tags/v*" in includes
    )
    return (
        detail.get("target") == "tag"
        and detail.get("enforcement") == "active"
        and include_matches
        and excludes == []
        and {"creation", "update", "deletion"}.issubset(rule_types)
    )


def verify_release_controls(
    evidence_dir: Path,
    repository_name: str,
    default_branch: str,
    release_tag: str,
    source_sha: str,
) -> None:
    if not re.fullmatch(r"[^/\s]+/[^/\s]+", repository_name):
        fail("workflow repository name is malformed")
    if not default_branch or default_branch.strip() != default_branch:
        fail("default branch is malformed")
    if not valid_release_tag(release_tag):
        fail("validated release tag is malformed")
    if SHA_RE.fullmatch(source_sha) is None:
        fail("validated source SHA is malformed")

    repository = load_json(evidence_dir / "repository.json")
    if not isinstance(repository, dict):
        fail("repository response is not an object")
    if str(repository.get("full_name", "")).casefold() != repository_name.casefold():
        fail("repository identity does not match the workflow repository")
    if repository.get("default_branch") != default_branch:
        fail("default branch changed after workflow dispatch")

    default_ref = load_json(evidence_dir / "default-ref.json")
    if not isinstance(default_ref, dict):
        fail("default branch ref response is not an object")
    ref_object = default_ref.get("object")
    if (
        default_ref.get("ref") != f"refs/heads/{default_branch}"
        or not isinstance(ref_object, dict)
        or ref_object.get("type") != "commit"
        or ref_object.get("sha") != source_sha
    ):
        fail("default branch no longer resolves to the validated source SHA")

    ci_runs = load_json(evidence_dir / "ci-runs.json")
    workflow_runs = ci_runs.get("workflow_runs") if isinstance(ci_runs, dict) else None
    if not isinstance(workflow_runs, list) or not any(
        isinstance(run, dict)
        and run.get("head_sha") == source_sha
        and run.get("head_branch") == default_branch
        and run.get("event") == "push"
        and run.get("status") == "completed"
        and run.get("conclusion") == "success"
        for run in workflow_runs
    ):
        fail("ci.yml has no successful protected-branch run at the validated source SHA")

    branch_rules = flatten_pages(
        load_json(evidence_dir / "branch-rules.json"), "branch rules"
    )
    rule_types = {rule.get("type") for rule in branch_rules}
    if not {"deletion", "non_fast_forward"}.issubset(rule_types):
        fail("default branch rules do not block deletion and force-pushes")
    if not has_required_solo_pull_request_rule(branch_rules):
        fail("default branch is missing the required solo-maintainer pull-request policy")
    if not has_required_status_rule(branch_rules):
        fail("default branch is missing strict required CI status check 'test'")

    environment = load_json(evidence_dir / "environment.json")
    if not isinstance(environment, dict) or environment.get("name") != "release":
        fail("release environment does not exist")
    if not isinstance(environment.get("protection_rules"), list):
        fail("release environment protection rules response is malformed")
    deployment_policy = environment.get("deployment_branch_policy")
    if (
        not isinstance(deployment_policy, dict)
        or deployment_policy.get("protected_branches") is not True
        or deployment_policy.get("custom_branch_policies") is not False
    ):
        fail("release environment must accept protected branches only")

    ruleset_ids = tag_ruleset_ids(evidence_dir / "tag-rulesets.json")
    if not ruleset_ids:
        fail("no tag rulesets were returned")
    if not any(
        tag_ruleset_protects_release(
            load_json(evidence_dir / f"tag-ruleset-{identifier}.json"),
            identifier,
        )
        for identifier in ruleset_ids
    ):
        fail(
            "no active release-tag ruleset restricts creation, update, and deletion"
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    identifiers = subparsers.add_parser(
        "list-ruleset-ids", help="print validated ruleset identifiers"
    )
    identifiers.add_argument("--input", required=True, type=Path)

    verify = subparsers.add_parser(
        "verify", help="verify captured GitHub release-control evidence"
    )
    verify.add_argument("--evidence-dir", required=True, type=Path)
    verify.add_argument("--repository", required=True)
    verify.add_argument("--default-branch", required=True)
    verify.add_argument("--release-tag", required=True)
    verify.add_argument("--source-sha", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "list-ruleset-ids":
            for identifier in tag_ruleset_ids(args.input):
                print(identifier)
        else:
            verify_release_controls(
                args.evidence_dir,
                args.repository,
                args.default_branch,
                args.release_tag,
                args.source_sha,
            )
            print("Public release controls verified for the exact protected source SHA.")
    except ReleaseControlError as exc:
        print(f"ERROR: release control gate failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
