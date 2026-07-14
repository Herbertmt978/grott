from pathlib import Path

import pytest

from grottext import ha as local_ha


def test_resolve_plugin_path_prefers_repo_example(tmp_path):
    example = tmp_path / "examples" / "Home Assistent" / "grott_ha.py"
    example.parent.mkdir(parents=True)
    example.write_text("# example plugin\n", encoding="utf-8")

    fallback = tmp_path / "grott_ha.py"
    fallback.write_text("# fallback plugin\n", encoding="utf-8")

    assert local_ha.resolve_plugin_path(tmp_path) == example


def test_resolve_plugin_path_falls_back_to_flat_docker_layout(tmp_path):
    fallback = tmp_path / "grott_ha.py"
    fallback.write_text("# fallback plugin\n", encoding="utf-8")

    assert local_ha.resolve_plugin_path(tmp_path) == fallback


def test_resolve_plugin_path_raises_when_plugin_is_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        local_ha.resolve_plugin_path(tmp_path)


def test_docker_runtime_copies_local_grottext_package():
    dockerfile = Path(__file__).resolve().parents[1] / "docker" / "dockerfile"
    text = dockerfile.read_text(encoding="utf-8")

    assert "COPY grottext /app/grottext" in text


def test_standard_docker_runtime_copies_proxy_protocol_module():
    dockerfile = Path(__file__).resolve().parents[1] / "docker" / "dockerfile"
    copy_lines = [
        line.split()
        for line in dockerfile.read_text(encoding="utf-8").splitlines()
        if line.startswith("COPY ")
    ]

    proxy_copy = next(parts for parts in copy_lines if "grottproxy.py" in parts)
    assert "grottprotocol.py" in proxy_copy
    assert proxy_copy[-1] == "/app/"


def test_legacy_rpi_docker_runtime_is_not_shipped():
    dockerfile = Path(__file__).resolve().parents[1] / "docker" / "dockerrpi"

    assert not dockerfile.exists()


def test_requirements_do_not_pull_external_grott_ha_plugin():
    requirements = (
        Path(__file__).resolve().parents[1] / "requirements.txt"
    ).read_text(encoding="utf-8")

    assert "grott-ha-plugin" not in requirements
