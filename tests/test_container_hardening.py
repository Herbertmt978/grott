from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re

import pytest
import yaml

from grottconf import Conf


ROOT = Path(__file__).resolve().parents[1]
BASE_IMAGE = (
    "python:3.11.15-alpine3.24@"
    "sha256:25976e9d34a0fab1f278cae931f34c8303d97bf0c0d7f85b6b4dcf641d7702a4"
)
DOCKERFILES = (ROOT / "docker" / "dockerfile", ROOT / "addons" / "grott" / "Dockerfile")
APPLICATION_MODULES = {
    "grott.py",
    "grottconf.py",
    "grottdata.py",
    "grottlayout.py",
    "grottprotocol.py",
    "grottproxy.py",
    "grottserver.py",
    "grottsniffer.py",
}
PACKAGED_LAYOUT_SOURCES = {
    "T06NNNNXMOD.json": ROOT / "examples" / "Record Layout" / "T06NNNNXMOD.json",
    "t060103xmax3.json": ROOT / "examples" / "Record Layout" / "t060103xmax3.json",
    "T06221b.json": ROOT / "examples" / "Record Layout" / "T06221b.json",
}
PACKAGED_LAYOUT_COPY = (
    'COPY ["examples/Record Layout/T06NNNNXMOD.json", '
    '"examples/Record Layout/t060103xmax3.json", '
    '"examples/Record Layout/T06221b.json", "/app/"]'
)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _logical_requirements(path: Path) -> list[str]:
    logical: list[str] = []
    pending = ""
    for raw_line in _text(path).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        pending = f"{pending} {line}".strip()
        if not line.endswith("\\"):
            logical.append(pending)
            pending = ""
    assert not pending, f"unfinished requirement in {path}"
    return logical


def test_hash_locks_are_complete_and_dev_lock_contains_runtime_and_tools():
    runtime = _logical_requirements(ROOT / "requirements.lock")
    development = _logical_requirements(ROOT / "requirements-dev.lock")

    assert runtime
    assert development
    assert all("==" in requirement and "--hash=sha256:" in requirement for requirement in runtime)
    assert all("==" in requirement and "--hash=sha256:" in requirement for requirement in development)
    for package in ("paho-mqtt", "requests", "influxdb-client", "libscrc", "pytest", "pyyaml", "ruff", "pip-audit"):
        assert any(requirement.lower().startswith(f"{package}==") for requirement in development)


def test_dev_inputs_bound_lint_and_dependency_audit_major_versions():
    requirements = _text(ROOT / "requirements-dev.txt").lower()

    assert re.search(r"^ruff>=[^\n]*<", requirements, re.MULTILINE)
    assert re.search(r"^pip-audit>=[^\n]*<", requirements, re.MULTILINE)


def test_dependabot_updates_wait_for_new_release_cooldown():
    dependabot = yaml.safe_load(_text(ROOT / ".github" / "dependabot.yml"))

    updates = dependabot["updates"]
    assert updates
    assert all(update.get("cooldown", {}).get("default-days", 0) >= 7 for update in updates)


def test_both_images_use_the_same_pinned_multistage_alpine_base_and_hash_lock():
    for path in DOCKERFILES:
        dockerfile = _text(path)
        builder, runtime = dockerfile.split("FROM ${PYTHON_IMAGE} AS runtime", 1)
        assert f"ARG PYTHON_IMAGE={BASE_IMAGE}" in dockerfile
        assert dockerfile.count("FROM ${PYTHON_IMAGE}") == 2
        assert "BUILD_FROM" not in dockerfile
        assert "build-base=0.5-r4" in dockerfile
        assert "tzdata=2026b-r0" in dockerfile
        assert "python -m venv /opt/venv" in dockerfile
        assert "--require-hashes" in dockerfile
        assert "requirements.lock" in dockerfile
        assert "COPY --from=builder /opt/venv /opt/venv" in dockerfile
        assert "ensurepip" in dockerfile
        assert "site-packages/pip" in dockerfile
        assert "site-packages/setuptools" in dockerfile
        assert "site-packages/distutils-precedence.pth" in dockerfile
        assert "/opt/venv/lib/python3.11/site-packages/wheel" not in builder
        assert "/opt/venv/lib/python3.11/site-packages/wheel-*.dist-info" not in builder
        assert "/opt/venv/bin/wheel" in builder
        assert "/usr/local/lib/python3.11/site-packages/wheel" in runtime
        assert "/usr/local/lib/python3.11/site-packages/wheel-*.dist-info" in runtime


def test_images_build_only_from_reviewed_root_context_and_copy_identical_sources():
    dockerignore = _text(ROOT / ".dockerignore")
    assert "/examples/Record Layout/*" in dockerignore
    assert {
        "!/examples/Record Layout/T06NNNNXMOD.json",
        "!/examples/Record Layout/t060103xmax3.json",
        "!/examples/Record Layout/T06221b.json",
    }.issubset(set(dockerignore.splitlines()))
    for path in DOCKERFILES:
        dockerfile = _text(path)
        assert "git clone" not in dockerfile
        assert "GROTT_REPO" not in dockerfile
        assert "GROTT_REF" not in dockerfile
        for module in APPLICATION_MODULES:
            assert module in dockerfile
        assert "COPY grottext /app/grottext" in dockerfile
        assert "COPY examples/grott.ini /app/grott.ini" in dockerfile
        assert 'COPY ["examples/Home Assistent/grott_ha.py", "/app/grott_ha.py"]' in dockerfile
        assert 'COPY ["examples/Record Layout/", "/app/"]' not in dockerfile
        assert PACKAGED_LAYOUT_COPY in dockerfile
        assert "COPY tools/container_healthcheck.py /usr/local/bin/container_healthcheck.py" in dockerfile
        assert "COPY tools/validate_container_artifact.py /usr/local/bin/validate_container_artifact.py" in dockerfile


def _packaged_recorddict(tmp_path: Path, monkeypatch) -> dict:
    conf = Conf.__new__(Conf)
    conf.verbose = False
    monkeypatch.chdir(tmp_path)
    conf.set_reclayouts()
    recorddict = deepcopy(conf.recorddict)

    for path in PACKAGED_LAYOUT_SOURCES.values():
        with path.open(encoding="utf-8") as handle:
            recorddict.update(json.load(handle))
    return recorddict


@pytest.mark.parametrize(
    ("layout", "field", "expected"),
    (
        (
            "T06NNNNXSPH",
            "SOC",
            {"value": 722, "length": 2, "type": "num", "divide": 1},
        ),
        (
            "T06NNNNXMIN",
            "bms_batterycurr",
            {"value": 1034, "length": 2, "type": "numx", "divide": 100},
        ),
        (
            "T06NNNNXMIN",
            "epv1today",
            {"value": 378, "length": 4, "type": "num", "divide": 10},
        ),
        (
            "T06NNNNXTL3",
            "datalogserial",
            {"value": 16, "length": 10, "type": "text", "incl": "yes"},
        ),
        (
            "T060120",
            "Current_l2",
            {
                "value": 192,
                "length": 4,
                "type": "num",
                "divide": 10,
                "incl": "yes",
            },
        ),
    ),
)
def test_packaged_external_layouts_preserve_builtin_contracts(
    tmp_path,
    monkeypatch,
    layout,
    field,
    expected,
):
    recorddict = _packaged_recorddict(tmp_path, monkeypatch)

    assert recorddict[layout][field] == expected


def test_root_and_packaged_mod_layout_sources_are_identical():
    assert _text(ROOT / "T06NNNNXMOD.json") == _text(
        PACKAGED_LAYOUT_SOURCES["T06NNNNXMOD.json"]
    )


def test_images_embed_immutable_source_revision_and_version_labels():
    for path in DOCKERFILES:
        dockerfile = _text(path)
        assert "ARG BUILD_VERSION=dev" in dockerfile
        assert "ARG VCS_REF=unknown" in dockerfile
        assert 'org.opencontainers.image.version="${BUILD_VERSION}"' in dockerfile
        assert 'org.opencontainers.image.revision="${VCS_REF}"' in dockerfile
        assert 'org.opencontainers.image.source="https://github.com/Herbertmt978/grott"' in dockerfile


def test_standard_image_is_non_root_read_only_compatible_and_has_passive_healthcheck():
    dockerfile = _text(ROOT / "docker" / "dockerfile")

    assert "addgroup -g 10001" in dockerfile
    assert "adduser -D -H -u 10001" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert "HOME=/tmp" in dockerfile
    assert "PYTHONDONTWRITEBYTECODE=1" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert "container_healthcheck.py" in dockerfile


def test_addon_image_reads_options_as_root_then_drops_to_fixed_identity():
    dockerfile = _text(ROOT / "addons" / "grott" / "Dockerfile")
    runtime = _text(ROOT / "addons" / "grott" / "run.sh")

    assert "su-exec=0.3-r0" in dockerfile
    assert "USER 10001:10001" not in dockerfile
    assert 'exec su-exec grott:grott "$GROTT_RUNNER" -u /app/grott.py -v' in runtime
    assert 'exec "$GROTT_RUNNER" -u /app/grott.py -v' in runtime


def test_compose_applies_container_runtime_hardening():
    compose = yaml.safe_load(_text(ROOT / "docker" / "docker-compose.yml"))
    service = compose["services"]["grott"]

    assert service["read_only"] is True
    assert service["tmpfs"] == ["/tmp"]
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert service["init"] is True
    assert service["user"] == "10001:10001"


def test_addon_config_uses_tmpfs_without_unused_share_mapping():
    config = yaml.safe_load(_text(ROOT / "addons" / "grott" / "config.yaml"))

    assert config["tmpfs"] is True
    assert "map" not in config
    assert set(config["arch"]) == {"aarch64", "amd64", "armv7", "i386"}


def test_legacy_armv6_dockerfile_is_retired():
    assert not (ROOT / "docker" / "dockerrpi").exists()


def test_ci_uses_locked_installs_audit_and_native_plus_multiarch_container_gates():
    workflow = yaml.safe_load(_text(ROOT / ".github" / "workflows" / "ci.yml"))
    steps = workflow["jobs"]["test"]["steps"]
    joined = "\n".join(str(step) for step in steps)

    assert "requirements-dev.lock" in joined
    assert "--require-hashes" in joined
    assert "pip-audit" in joined and "requirements.lock" in joined
    assert "Build native Docker runtime image" in joined
    assert "Build native Home Assistant add-on image" in joined
    assert "Smoke test native container images" in joined
    assert "aquasec/trivy:0.72.0@sha256:cffe3f5161a47a6823fbd23d985795b3ed72a4c806da4c4df16266c02accdd6f" in joined
    assert "linux/amd64,linux/arm64,linux/arm/v7,linux/386" in joined
    assert "BUILD_VERSION=ci" in joined
    assert "VCS_REF=${{ github.sha }}" in joined
    smoke = next(step["run"] for step in steps if step.get("name") == "Smoke test native container images")
    addon_start = smoke.split("--name grott-ci-addon", 1)[1].split("wait_for_healthy", 1)[0]
    assert "--cap-drop ALL" in addon_start
    assert "--cap-add SETUID --cap-add SETGID" in addon_start
    assert "--security-opt no-new-privileges:true" in addon_start
    assert "json.dumps" in smoke
    assert "0:0 600" in smoke
    assert "/^Gid:/" in smoke
    assert "/^CapEff:/" in smoke
    assert "0000000000000000" in smoke


def test_ci_captures_actual_attached_service_stderr_for_both_images():
    workflow = yaml.safe_load(_text(ROOT / ".github" / "workflows" / "ci.yml"))
    smoke = next(
        step["run"]
        for step in workflow["jobs"]["test"]["steps"]
        if step.get("name") == "Smoke test native container images"
    )

    for container, image, prefix in (
        ("grott-ci-runtime", "grott-ci-runtime:ci", "runtime"),
        ("grott-ci-addon", "grott-ci-addon:ci", "addon"),
    ):
        invocation = re.search(
            rf"docker run --name {container}\s+.*?{re.escape(image)}\s+"
            rf">\s*\"\$\{{{prefix}_stdout\}}\"\s+"
            rf"2>\s*\"\$\{{{prefix}_stderr\}}\"\s*&",
            smoke,
            re.DOTALL,
        )
        assert invocation, f"{container} must run attached in the background with split logs"
        assert "--detach" not in invocation.group(0)
        assert f'test ! -s "${{{prefix}_stderr}}"' in smoke
        assert f'{prefix}_run_pid=$!' in smoke

    assert 'wait "${runtime_run_pid}"' in smoke
    assert 'wait "${addon_run_pid}"' in smoke


def test_ci_executes_complete_artifact_validator_for_both_native_images():
    workflow = yaml.safe_load(_text(ROOT / ".github" / "workflows" / "ci.yml"))
    step = next(
        step
        for step in workflow["jobs"]["test"]["steps"]
        if step.get("name") == "Validate native runtime artifacts"
    )
    script = step["run"]

    assert "for image in grott-ci-runtime:ci grott-ci-addon:ci" in script
    assert "--entrypoint python" in script
    assert '"${image}" /usr/local/bin/validate_container_artifact.py' in script


def test_ci_and_publish_register_qemu_before_one_serialized_buildkit_builder():
    for workflow_path in (
        ROOT / ".github" / "workflows" / "ci.yml",
        ROOT / ".github" / "workflows" / "publish-ghcr.yml",
    ):
        workflow = yaml.safe_load(_text(workflow_path))
        jobs_with_multiarch_builds = []
        for job_name, job in workflow["jobs"].items():
            job_steps = job.get("steps", [])
            multiarch_indexes = [
                index
                for index, step in enumerate(job_steps)
                if str(step.get("uses", "")).startswith("docker/build-push-action@")
                and set(
                    platform.strip()
                    for platform in str(step.get("with", {}).get("platforms", "")).split(",")
                    if platform.strip()
                )
                == {"linux/amd64", "linux/arm64", "linux/arm/v7", "linux/386"}
            ]
            if multiarch_indexes:
                jobs_with_multiarch_builds.append((job_name, job_steps, multiarch_indexes))

        assert len(jobs_with_multiarch_builds) == 1
        job_name, job_steps, multiarch_indexes = jobs_with_multiarch_builds[0]
        qemu_indexes = [
            index
            for index, step in enumerate(job_steps)
            if str(step.get("uses", "")).startswith("docker/setup-qemu-action@")
        ]
        buildx_indexes = [
            index
            for index, step in enumerate(job_steps)
            if str(step.get("uses", "")).startswith("docker/setup-buildx-action@")
        ]

        assert len(qemu_indexes) == 1, f"{workflow_path}:{job_name} must register QEMU once"
        assert len(buildx_indexes) == 1, f"{workflow_path}:{job_name} must create one builder"
        assert qemu_indexes[0] < buildx_indexes[0] < min(multiarch_indexes)
        config = str(
            job_steps[buildx_indexes[0]]
            .get("with", {})
            .get("buildkitd-config-inline", "")
        )
        assert re.search(r"\[worker\.oci\]\s+max-parallelism\s*=\s*1", config)


def test_publish_passes_version_and_source_revision_to_both_builds_without_grott_ref():
    workflow = yaml.safe_load(_text(ROOT / ".github" / "workflows" / "publish-ghcr.yml"))
    builds = [
        step
        for step in workflow["jobs"]["publish"]["steps"]
        if str(step.get("uses", "")).startswith("docker/build-push-action@")
    ]

    assert len(builds) == 2
    for build in builds:
        build_args = str(build["with"].get("build-args", ""))
        assert "BUILD_VERSION=${{ needs.validate.outputs.version }}" in build_args
        assert "VCS_REF=${{ needs.validate.outputs.source_sha }}" in build_args
        assert "GROTT_REF" not in build_args
