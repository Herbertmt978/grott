import json
import os
from pathlib import Path
import shutil
import subprocess

import yaml

from tools import validate_ha_addon_repo as validator


ROOT = Path(__file__).resolve().parents[1]
ADDON_DIR = ROOT / "addons" / "grott"
MISSING_OPTIONS = object()


def run_addon_runtime(tmp_path, name, options=MISSING_OPTIONS, expect_success=True):
    if os.name == "nt":
        shell = Path(r"C:\Program Files\Git\bin\sh.exe")
    else:
        shell = Path(shutil.which("sh") or "/bin/sh")
    assert shell.is_file()

    runner = tmp_path / f"{name}-capture-env.sh"
    runner.write_bytes(
        b"#!/bin/sh\n"
        b"python - \"$CAPTURE_PATH\" <<'PY'\n"
        b"import json, os, sys\n"
        b"keys = ('gnomqtt', 'gextension', 'gextname', 'gextvar')\n"
        b"with open(sys.argv[1], 'w', encoding='utf-8') as handle:\n"
        b"    json.dump({key: os.environ.get(key) for key in keys}, handle)\n"
        b"PY\n"
    )
    runner.chmod(0o755)

    options_path = tmp_path / f"{name}-options.json"
    capture_path = tmp_path / f"{name}-environment.json"
    if options is not MISSING_OPTIONS:
        options_path.write_text(json.dumps(options), encoding="utf-8")
    environment = os.environ.copy()
    for key in ("gnomqtt", "gextension", "gextname", "gextvar"):
        environment.pop(key, None)
    environment.update(
        {
            "OPTIONS": options_path.as_posix(),
            "GROTT_RUNNER": runner.as_posix(),
            "CAPTURE_PATH": capture_path.as_posix(),
        }
    )
    result = subprocess.run(
        [str(shell), (ADDON_DIR / "run.sh").as_posix()],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    if not expect_success:
        return result, capture_path

    assert result.returncode == 0, result.stderr
    return json.loads(capture_path.read_text(encoding="utf-8"))


def test_addon_runner_uses_lf_line_endings_only():
    runtime = (ADDON_DIR / "run.sh").read_bytes()

    assert b"\r" not in runtime
    assert runtime.endswith(b"\n")


def test_addon_schema_exposes_proxy_mode_only():
    config = yaml.safe_load((ADDON_DIR / "config.yaml").read_text(encoding="utf-8"))

    assert config["options"]["mode"] == "proxy"
    assert config["schema"]["mode"] == "list(proxy)"


def test_addon_schema_exposes_supported_ha_entity_profiles():
    config = yaml.safe_load((ADDON_DIR / "config.yaml").read_text(encoding="utf-8"))

    assert config["options"]["ha_entity_profile"] == "v0_1_9_standard"
    assert config["schema"]["ha_entity_profile"] == "list(v0_1_9_standard|all)"


def test_addon_config_uses_tmpfs_and_does_not_mount_share():
    config = yaml.safe_load((ADDON_DIR / "config.yaml").read_text(encoding="utf-8"))

    assert config["tmpfs"] is True
    assert "map" not in config


def test_ha_plugin_disables_native_mqtt_publishing():
    runtime = (ADDON_DIR / "run.sh").read_text(encoding="utf-8")
    ha_block = runtime.split(
        'if [ "$(json_get ha_plugin true)" = "True" ]; then', 1
    )[1].split("\nfi", 1)[0]

    assert "export gnomqtt=True" in ha_block


def test_extension_mapping_is_emitted_as_json():
    runtime = (ADDON_DIR / "run.sh").read_text(encoding="utf-8")

    assert "print(json.dumps(payload" in runtime
    assert "print(repr(payload))" not in runtime


def test_addon_runner_drops_root_after_reading_options():
    runtime = (ADDON_DIR / "run.sh").read_text(encoding="utf-8")

    assert 'if [ "$(id -u)" -eq 0 ]; then' in runtime
    assert 'exec su-exec grott:grott "$GROTT_RUNNER" -u /app/grott.py -v' in runtime
    assert runtime.rstrip().endswith('exec "$GROTT_RUNNER" -u /app/grott.py -v')


def test_addon_runtime_translates_ha_plugin_options_behaviorally(tmp_path):
    enabled = run_addon_runtime(
        tmp_path,
        "enabled",
        {
            "ha_plugin": True,
            "mqtt_host": "mqtt.synthetic.local",
            "mqtt_port": 2883,
            "mqtt_retain": True,
            "mqtt_user": "synthetic-user",
            "mqtt_password": "synthetic-password",
            "ha_entity_profile": "all",
        },
    )
    assert enabled["gnomqtt"] == "True"
    assert json.loads(enabled["gextvar"]) == {
        "ha_mqtt_host": "mqtt.synthetic.local",
        "ha_mqtt_port": 2883,
        "ha_mqtt_retain": True,
        "ha_mqtt_user": "synthetic-user",
        "ha_mqtt_password": "synthetic-password",
        "ha_entity_profile": "all",
    }

    disabled = run_addon_runtime(tmp_path, "disabled", {"ha_plugin": False})
    assert disabled == {
        "gnomqtt": None,
        "gextension": None,
        "gextname": None,
        "gextvar": None,
    }


def test_addon_runtime_defaults_ha_plugin_to_enabled_when_options_file_is_missing(
    tmp_path,
):
    environment = run_addon_runtime(tmp_path, "missing-options-file")

    assert environment["gnomqtt"] == "True"
    assert environment["gextension"] == "True"
    assert environment["gextname"] == "grottext.ha"
    assert json.loads(environment["gextvar"]) == {
        "ha_mqtt_host": "core-mosquitto",
        "ha_mqtt_port": 1883,
        "ha_mqtt_retain": False,
        "ha_entity_profile": "v0_1_9_standard",
    }


def test_addon_runtime_defaults_ha_plugin_to_enabled_when_key_is_missing(tmp_path):
    environment = run_addon_runtime(tmp_path, "missing-ha-plugin-key", {})

    assert environment["gnomqtt"] == "True"
    assert environment["gextension"] == "True"
    assert environment["gextname"] == "grottext.ha"
    assert json.loads(environment["gextvar"]) == {
        "ha_mqtt_host": "core-mosquitto",
        "ha_mqtt_port": 1883,
        "ha_mqtt_retain": False,
        "ha_entity_profile": "v0_1_9_standard",
    }


def test_addon_runtime_rejects_unsupported_ha_entity_profile(tmp_path):
    result, capture_path = run_addon_runtime(
        tmp_path,
        "unsupported-ha-entity-profile",
        {"ha_entity_profile": "everything"},
        expect_success=False,
    )

    assert result.returncode != 0
    assert "invalid ha_entity_profile" in result.stderr
    assert not capture_path.exists()


def test_validator_rejects_non_proxy_addon_schema(monkeypatch):
    config = yaml.safe_load((ADDON_DIR / "config.yaml").read_text(encoding="utf-8"))
    config["schema"]["mode"] = "list(proxy|server|sniff)"
    monkeypatch.setattr(validator, "load_yaml", lambda _path: config)
    errors = []

    validator.validate_addon_config(errors)

    assert any("proxy mode only" in error for error in errors)


def test_validator_requires_tmpfs_and_rejects_unused_share_mapping(monkeypatch):
    config = yaml.safe_load((ADDON_DIR / "config.yaml").read_text(encoding="utf-8"))
    config.pop("tmpfs", None)
    config["map"] = ["share:rw"]
    monkeypatch.setattr(validator, "load_yaml", lambda _path: config)
    errors = []

    validator.validate_addon_config(errors)

    assert any("tmpfs" in error for error in errors)
    assert any("share mapping" in error for error in errors)


def test_validator_requires_native_mqtt_disable_in_ha_block(monkeypatch, tmp_path):
    runtime = tmp_path / "run.sh"
    runtime.write_text(
        'if [ "$(json_get ha_plugin true)" = "True" ]; then\n'
        "  export gextension=True\n"
        "fi\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(validator, "RUN_PATH", runtime, raising=False)
    errors = []

    validator.validate_addon_config(errors)

    assert any("gnomqtt=True" in error for error in errors)


def test_validator_requires_json_extension_mapping(monkeypatch, tmp_path):
    runtime = tmp_path / "run.sh"
    runtime.write_text(
        'if [ "$(json_get ha_plugin true)" = "True" ]; then\n'
        "  export gnomqtt=True\n"
        "  print(repr(payload))\n"
        "fi\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(validator, "RUN_PATH", runtime)
    errors = []

    validator.validate_addon_config(errors)

    assert any("json.dumps" in error for error in errors)


def test_validator_rejects_commented_or_dead_runtime_contract(monkeypatch, tmp_path):
    runtime = tmp_path / "run.sh"
    runtime.write_text(
        'if [ "$(json_get ha_plugin true)" = "True" ]; then\n'
        "  # export gnomqtt=True\n"
        "  echo 'print(json.dumps(payload))'\n"
        "fi\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(validator, "RUN_PATH", runtime)
    errors = []

    validator.validate_addon_config(errors)

    assert any("gnomqtt=True" in error for error in errors)
    assert any("json.dumps" in error for error in errors)


def test_validator_rejects_non_lf_addon_runner(monkeypatch, tmp_path):
    runtime = tmp_path / "run.sh"
    runtime.write_bytes((ADDON_DIR / "run.sh").read_bytes().replace(b"\n", b"\r\n"))
    monkeypatch.setattr(validator, "RUN_PATH", runtime)
    errors = []

    validator.validate_addon_config(errors)

    assert any("LF-only line endings" in error for error in errors)


def test_validator_rejects_remote_clone_or_mutable_addon_base(monkeypatch, tmp_path):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "ARG BUILD_FROM=python:3.11-alpine\n"
        "FROM ${BUILD_FROM}\n"
        "RUN git clone https://example.invalid/grott /app\n"
        "LABEL io.hass.version=x io.hass.type=app io.hass.arch=x\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(validator, "DOCKERFILE_PATH", dockerfile)
    errors = []

    validator.validate_files(errors)

    assert any("pinned Python base" in error for error in errors)
    assert any("repository root context" in error for error in errors)
