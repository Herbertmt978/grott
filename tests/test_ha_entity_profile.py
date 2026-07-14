import importlib.util
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace

import pytest

from grottconf import Conf


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_PATH = ROOT / "examples" / "Home Assistent" / "grott_ha.py"
V019_GOLDEN_PATH = ROOT / "tests" / "fixtures" / "ha_v0_1_9_standard_discovery.json"
spec = importlib.util.spec_from_file_location("grott_ha_entity_profile", PLUGIN_PATH)
grott_ha = importlib.util.module_from_spec(spec)
spec.loader.exec_module(grott_ha)


def mod_layout():
    return json.loads(
        (ROOT / "examples" / "Record Layout" / "T06NNNNXMOD.json").read_text(
            encoding="utf-8"
        )
    )["T06NNNNXMOD"]


def generic_layout():
    conf = Conf.__new__(Conf)
    conf.verbose = False
    previous_cwd = Path.cwd()
    with tempfile.TemporaryDirectory() as directory:
        try:
            os.chdir(directory)
            with contextlib.redirect_stdout(io.StringIO()):
                conf.set_reclayouts()
        finally:
            os.chdir(previous_cwd)
    return conf.recorddict["T06NNNNX"]


def decoded_values(layout, include_excluded=False):
    return {
        key: index
        for index, (key, entry) in enumerate(layout.items())
        if isinstance(entry, dict)
        and "length" in entry
        and (include_excluded or entry.get("incl") != "no")
    }


def make_conf(layout_name="T06NNNNXMOD", profile=None, includeall=False):
    extvar = {"ha_mqtt_host": "localhost", "ha_mqtt_port": 1883}
    if profile is not None:
        extvar["ha_entity_profile"] = profile
    return SimpleNamespace(
        layout=layout_name,
        recorddict={
            "T06NNNNXMOD": mod_layout(),
            "T06NNNNX": generic_layout(),
        },
        extvar=extvar,
        verbose=False,
        includeall=includeall,
    )


@pytest.fixture(autouse=True)
def reset_handler_state():
    grott_ha.MqttStateHandler._MqttStateHandler__pv_config = {}


def capture_publishers(monkeypatch, cleanup_failure=False):
    calls = []
    cleanup_attempts = 0

    def multiple(conf, messages):
        nonlocal cleanup_attempts
        batch = list(messages)
        calls.append(("multiple", batch))
        if batch and all(message["payload"] == "" for message in batch):
            cleanup_attempts += 1
            if cleanup_failure and cleanup_attempts == 1:
                raise RuntimeError("broker rejected cleanup")
        return True

    def single(conf, topic, payload, retain=False):
        calls.append(("single", {"topic": topic, "payload": payload, "retain": retain}))
        return True

    monkeypatch.setattr(grott_ha, "publish_multiple", multiple)
    monkeypatch.setattr(grott_ha, "publish_single", single)
    return calls


def discovery_messages(calls):
    return [message for kind, batch in calls if kind == "multiple" for message in batch]


def state_messages(calls):
    return [item for kind, item in calls if kind == "single"]


def desired_batches(calls):
    return [
        batch
        for kind, batch in calls
        if kind == "multiple" and batch and all(message["payload"] for message in batch)
    ]


def cleanup_batches(calls):
    return [
        batch
        for kind, batch in calls
        if kind == "multiple"
        and batch
        and all(message["payload"] == "" for message in batch)
    ]


def apply_retained(retained, calls):
    for message in discovery_messages(calls):
        apply_discovery_batch(retained, [message])


def apply_discovery_batch(retained, messages):
    for message in messages:
        if message["payload"]:
            retained[message["topic"]] = message["payload"]
        else:
            retained.pop(message["topic"], None)


def standard_topics(device):
    return {
        f"homeassistant/sensor/grott/{device}_{key}/config"
        for key in grott_ha.V0_1_9_STANDARD_KEYS
    }


def approved_candidate_golden():
    golden = json.loads(V019_GOLDEN_PATH.read_text(encoding="utf-8"))
    assert golden["source_image"] == (
        "sha256:e9314693651e0cce82c603b53f88c66ae4757d93e09b97a24c56070c845d2351"
    )
    assert golden["source_release"] == "v0.1.9-beta"
    assert len(golden["configs"]) == 32

    expected = copy.deepcopy(golden["configs"])
    by_identity = {
        item["payload"]["unique_id"].removeprefix("grott_GOLDEN_DEVICE_"): item["payload"]
        for item in expected
    }
    for item in expected:
        item["payload"]["origin"] = {"name": "Grott", "sw_version": "0.0.8"}
    by_identity["pvstatus"]["value_template"] = "{{ value_json.pvstatus | float / 1 }}"
    by_identity["totworktime"]["unit_of_measurement"] = "h"
    by_identity["pvpowerout"]["value_template"] = (
        "{% if value_json.pac is defined %}{{ value_json.pac | float / 10 }}"
        "{% elif value_json.pvfrequentie is defined %}"
        "{{ value_json.pvpowerout | float / 10 }}{% endif %}"
    )
    by_identity["pvfrequentie"]["value_template"] = (
        "{% if value_json.pvfrequency is defined %}"
        "{{ value_json.pvfrequency | float / 100 }}"
        "{% elif value_json.pvfrequentie is defined %}"
        "{{ value_json.pvfrequentie | float / 100 }}{% endif %}"
    )
    by_identity["pvipmtemperature"]["value_template"] = (
        "{% if value_json.comboardtemperature is defined %}"
        "{{ value_json.comboardtemperature | float / 10 }}"
        "{% elif value_json.pvfrequentie is defined %}"
        "{{ value_json.pvipmtemperature | float / 10 }}{% endif %}"
    )
    return expected


def test_default_generic_profile_matches_immutable_v019_payload_golden(monkeypatch):
    calls = capture_publishers(monkeypatch)
    values = decoded_values(generic_layout())

    assert grott_ha.grottext(
        make_conf(layout_name="T06NNNNX"),
        "",
        json.dumps({"device": "GOLDEN_DEVICE", "values": values}),
    ) == 0

    actual = [
        {"topic": message["topic"], "payload": json.loads(message["payload"])}
        for message in desired_batches(calls)[0]
    ]
    expected = approved_candidate_golden()
    assert actual == expected
    golden_identities = [
        item["payload"]["unique_id"].removeprefix("grott_GOLDEN_DEVICE_")
        for item in expected
    ]
    assert golden_identities == grott_ha.V0_1_9_STANDARD_KEYS


def test_default_mod_profile_has_frozen_32_configs_aliases_and_full_state(monkeypatch):
    conf = make_conf()
    calls = capture_publishers(monkeypatch)
    values = decoded_values(mod_layout())

    assert grott_ha.grottext(conf, "", json.dumps({"device": "MOD1", "values": values})) == 0

    desired = [message for message in discovery_messages(calls) if message["payload"]]
    tombstones = [message for message in discovery_messages(calls) if not message["payload"]]
    assert len(desired) == 32
    assert all(message["qos"] == 1 and message["retain"] for message in desired)
    assert [
        message["topic"].split("/MOD1_", 1)[1].removesuffix("/config")
        for message in desired
    ] == grott_ha.V0_1_9_STANDARD_KEYS
    configs = {message["topic"]: json.loads(message["payload"]) for message in desired}
    assert [
        json.loads(message["payload"])["unique_id"] for message in desired
    ] == [f"grott_MOD1_{key}" for key in grott_ha.V0_1_9_STANDARD_KEYS]
    power = configs["homeassistant/sensor/grott/MOD1_pvpowerout/config"]
    frequency = configs["homeassistant/sensor/grott/MOD1_pvfrequentie/config"]
    temperature = configs["homeassistant/sensor/grott/MOD1_pvipmtemperature/config"]
    assert power["unique_id"] == "grott_MOD1_pvpowerout"
    assert power["value_template"] == (
        "{% if value_json.pac is defined %}{{ value_json.pac | float / 10 }}"
        "{% elif value_json.pvfrequentie is defined %}"
        "{{ value_json.pvpowerout | float / 10 }}{% endif %}"
    )
    assert frequency["value_template"] == (
        "{% if value_json.pvfrequency is defined %}{{ value_json.pvfrequency | float / 100 }}"
        "{% elif value_json.pvfrequentie is defined %}"
        "{{ value_json.pvfrequentie | float / 100 }}{% endif %}"
    )
    assert temperature["value_template"] == (
        "{% if value_json.comboardtemperature is defined %}{{ value_json.comboardtemperature | float / 10 }}"
        "{% elif value_json.pvfrequentie is defined %}"
        "{{ value_json.pvipmtemperature | float / 10 }}{% endif %}"
    )
    assert configs["homeassistant/sensor/grott/MOD1_totworktime/config"]["unit_of_measurement"] == "h"
    assert configs["homeassistant/sensor/grott/MOD1_pvstatus/config"]["value_template"] == "{{ value_json.pvstatus | float / 1 }}"
    assert all("origin" in config for config in configs.values())
    assert all("entity_category" not in config for config in configs.values())
    assert all(message["qos"] == 1 and message["retain"] for message in tombstones)
    assert all("MOD1" in message["topic"] for message in tombstones)
    state = json.loads(state_messages(calls)[0]["payload"])
    normalized_values = grott_ha.normalize_values(values)
    assert set(normalized_values).issubset(state)
    assert state["pvpowerout"] == normalized_values["pvpowerout"]


def test_all_mod_profile_has_171_configs_and_marks_only_non_core_diagnostic(monkeypatch):
    conf = make_conf(profile="all")
    calls = capture_publishers(monkeypatch)

    assert grott_ha.grottext(conf, "", json.dumps({"device": "MOD1", "values": decoded_values(mod_layout())})) == 0

    desired = [message for message in discovery_messages(calls) if message["payload"]]
    configs = {message["topic"]: json.loads(message["payload"]) for message in desired}
    assert len(desired) == 171
    assert "homeassistant/sensor/grott/MOD1_raw_pvpowerout_r3019/config" in configs
    assert "homeassistant/sensor/grott/MOD1_pac/config" not in configs
    assert "homeassistant/sensor/grott/MOD1_pvfrequency/config" not in configs
    assert "homeassistant/sensor/grott/MOD1_comboardtemperature/config" not in configs
    assert configs["homeassistant/sensor/grott/MOD1_raw_pvpowerout_r3019/config"]["entity_category"] == "diagnostic"
    assert configs["homeassistant/sensor/grott/MOD1_raw_pvpowerout_r3019/config"]["unique_id"] == "grott_MOD1_raw_pvpowerout_r3019"
    assert configs["homeassistant/sensor/grott/MOD1_raw_pvpowerout_r3019/config"]["value_template"] == "{{ value_json.pvpowerout | float / 10 }}"
    assert "entity_category" not in configs["homeassistant/sensor/grott/MOD1_pvpowerout/config"]
    assert all(message["qos"] == 1 and message["retain"] for message in desired)


def test_generic_profile_preserves_existing_discovery_and_alias_templates(monkeypatch):
    conf = make_conf(layout_name="T06NNNNX")
    calls = capture_publishers(monkeypatch)
    values = decoded_values(generic_layout())
    values["datalogserial"] = "LOGGER1"

    assert grott_ha.grottext(conf, "", json.dumps({"device": "GEN1", "values": values})) == 0

    desired = [message for message in discovery_messages(calls) if message["payload"]]
    configs = {message["topic"]: json.loads(message["payload"]) for message in desired}
    assert len(desired) == 32
    assert configs["homeassistant/sensor/grott/GEN1_pvpowerout/config"]["value_template"] == (
        "{% if value_json.pac is defined %}{{ value_json.pac | float / 10 }}"
        "{% elif value_json.pvfrequentie is defined %}"
        "{{ value_json.pvpowerout | float / 10 }}{% endif %}"
    )
    assert configs["homeassistant/sensor/grott/GEN1_pvipmtemperature/config"]["value_template"] == (
        "{% if value_json.comboardtemperature is defined %}"
        "{{ value_json.comboardtemperature | float / 10 }}"
        "{% elif value_json.pvfrequentie is defined %}"
        "{{ value_json.pvipmtemperature | float / 10 }}{% endif %}"
    )
    state = json.loads(state_messages(calls)[0]["payload"])
    assert {key: value for key, value in state.items() if key != "grott_last_push"} == values


def test_generic_all_is_a_superset_of_the_frozen_compatibility_map(monkeypatch):
    conf = make_conf(layout_name="T06NNNNX", profile="all")
    calls = capture_publishers(monkeypatch)

    assert grott_ha.grottext(
        conf,
        "",
        json.dumps({"device": "GEN1", "values": decoded_values(generic_layout())}),
    ) == 0

    desired = [message for message in discovery_messages(calls) if message["payload"]]
    identities = {
        json.loads(message["payload"])["unique_id"].removeprefix("grott_GEN1_")
        for message in desired
    }
    assert set(grott_ha.V0_1_9_STANDARD_KEYS).issubset(identities)
    assert len(desired) == len(identities) == 32


def test_mod_all_uses_the_layout_definition_when_a_packet_is_partial(monkeypatch):
    conf = make_conf(profile="all")
    calls = capture_publishers(monkeypatch)

    assert grott_ha.grottext(
        conf,
        "",
        json.dumps({"device": "MOD1", "values": {"pvserial": "TEST", "pac": 123}}),
    ) == 0

    assert len(desired_batches(calls)[0]) == 171


def test_invalid_profile_fails_clearly_without_discovery_or_state(monkeypatch, capsys):
    conf = make_conf(profile="everything")
    calls = capture_publishers(monkeypatch)

    assert grott_ha.grottext(conf, "", json.dumps({"device": "MOD1", "values": decoded_values(mod_layout())})) == 6
    assert calls == []
    assert "Invalid ha_entity_profile" in capsys.readouterr().out


def test_profile_switch_publishes_desired_before_same_device_tombstones(monkeypatch):
    calls = capture_publishers(monkeypatch)
    values = decoded_values(mod_layout())

    assert grott_ha.grottext(make_conf(profile="all"), "", json.dumps({"device": "MOD1", "values": values})) == 0
    calls.clear()
    assert grott_ha.grottext(make_conf(), "", json.dumps({"device": "MOD1", "values": values})) == 0

    assert calls[0][0] == "multiple"
    assert all(message["payload"] for message in calls[0][1])
    assert calls[1][0] == "multiple"
    assert all(message["payload"] == "" and message["qos"] == 1 for message in calls[1][1])
    assert all("/MOD1_" in message["topic"] for message in calls[1][1])
    assert "homeassistant/sensor/grott/MOD1_raw_pvpowerout_r3019/config" in {
        message["topic"] for message in calls[1][1]
    }
    assert calls[2][0] == "single"


def test_repeated_packet_does_not_repeat_successful_discovery(monkeypatch):
    calls = capture_publishers(monkeypatch)
    conf = make_conf()
    message = json.dumps({"device": "MOD1", "values": decoded_values(mod_layout())})

    assert grott_ha.grottext(conf, "", message) == 0
    calls.clear()
    assert grott_ha.grottext(conf, "", message) == 0

    assert [kind for kind, _ in calls] == ["single"]


def test_default_discovery_is_stable_across_mod_generic_mod_packets(monkeypatch):
    calls = capture_publishers(monkeypatch)

    for layout_name, layout in (
        ("T06NNNNXMOD", mod_layout()),
        ("T06NNNNX", generic_layout()),
        ("T06NNNNXMOD", mod_layout()),
    ):
        assert grott_ha.grottext(
            make_conf(layout_name=layout_name),
            "",
            json.dumps({"device": "MIXED1", "values": decoded_values(layout)}),
        ) == 0

    assert len(desired_batches(calls)) == 1
    assert len(desired_batches(calls)[0]) == 32
    assert len(state_messages(calls)) == 3


def test_failed_cleanup_retries_exact_batch_after_layout_change(monkeypatch):
    calls = capture_publishers(monkeypatch, cleanup_failure=True)

    assert grott_ha.grottext(
        make_conf(),
        "",
        json.dumps({"device": "MIXED1", "values": decoded_values(mod_layout())}),
    ) == 0
    failed_batch = list(cleanup_batches(calls)[0])
    assert len(state_messages(calls)) == 1

    assert grott_ha.grottext(
        make_conf(layout_name="T06NNNNX"),
        "",
        json.dumps({"device": "MIXED1", "values": decoded_values(generic_layout())}),
    ) == 0

    assert cleanup_batches(calls)[1] == failed_batch
    assert len(desired_batches(calls)) == 1
    assert len(state_messages(calls)) == 2


def test_failed_cleanup_batch_is_carried_across_profile_signature_change(monkeypatch):
    calls = capture_publishers(monkeypatch, cleanup_failure=True)
    mod_values = decoded_values(mod_layout())

    assert grott_ha.grottext(
        make_conf(profile="all"),
        "",
        json.dumps({"device": "CARRY1", "values": mod_values}),
    ) == 0
    failed_batch = list(cleanup_batches(calls)[0])
    assert len(state_messages(calls)) == 1

    assert grott_ha.grottext(
        make_conf(layout_name="T06NNNNX", profile="all"),
        "",
        json.dumps(
            {"device": "CARRY1", "values": decoded_values(generic_layout())}
        ),
    ) == 0
    assert cleanup_batches(calls)[1] == failed_batch
    assert len(desired_batches(calls)) == 2
    assert len(state_messages(calls)) == 2

    assert grott_ha.grottext(
        make_conf(layout_name="T06NNNNX", profile="all"),
        "",
        json.dumps(
            {"device": "CARRY1", "values": decoded_values(generic_layout())}
        ),
    ) == 0
    assert len(cleanup_batches(calls)) == 3
    assert len(state_messages(calls)) == 3


@pytest.mark.parametrize("profile", [None, "all"])
def test_generic_first_packet_after_restart_cleans_preloaded_mod_all_topics(
    monkeypatch, profile
):
    retained = {}
    calls = capture_publishers(monkeypatch)
    assert grott_ha.grottext(
        make_conf(profile="all"),
        "",
        json.dumps({"device": "UPGRADE1", "values": decoded_values(mod_layout())}),
    ) == 0
    apply_retained(retained, calls)
    assert len(retained) == 171

    grott_ha.MqttStateHandler._MqttStateHandler__pv_config = {}
    calls.clear()
    assert grott_ha.grottext(
        make_conf(layout_name="T06NNNNX", profile=profile),
        "",
        json.dumps({"device": "UPGRADE1", "values": decoded_values(generic_layout())}),
    ) == 0
    apply_retained(retained, calls)

    expected = {
        f"homeassistant/sensor/grott/UPGRADE1_{key}/config"
        for key in grott_ha.V0_1_9_STANDARD_KEYS
    }
    assert set(retained) == expected


def test_cleanup_failure_retries_without_suppressing_state_or_other_device(monkeypatch):
    calls = capture_publishers(monkeypatch, cleanup_failure=True)
    values = decoded_values(mod_layout())

    assert grott_ha.grottext(make_conf(), "", json.dumps({"device": "MOD1", "values": values})) == 0
    assert grott_ha.grottext(make_conf(), "", json.dumps({"device": "MOD2", "values": values})) == 0
    assert grott_ha.grottext(make_conf(), "", json.dumps({"device": "MOD1", "values": values})) == 0

    assert len(state_messages(calls)) == 3
    cleanup = cleanup_batches(calls)
    assert len(cleanup) == 3
    assert all("MOD1" in message["topic"] for message in cleanup[0])
    assert all("MOD2" in message["topic"] for message in cleanup[1])
    assert all("MOD1" in message["topic"] for message in cleanup[2])


def test_partially_delivered_cleanup_retries_full_batch_and_converges(monkeypatch):
    retained = {}
    initial_calls = capture_publishers(monkeypatch)
    values = decoded_values(mod_layout())

    assert grott_ha.grottext(
        make_conf(profile="all"),
        "",
        json.dumps({"device": "PARTIAL1", "values": values}),
    ) == 0
    apply_retained(retained, initial_calls)
    assert len(retained) == 171

    calls = []
    cleanup_attempts = 0

    def multiple_with_partial_delivery(conf, messages):
        nonlocal cleanup_attempts
        batch = list(messages)
        calls.append(("multiple", batch))
        is_cleanup = batch and all(message["payload"] == "" for message in batch)
        if is_cleanup:
            cleanup_attempts += 1
            if cleanup_attempts == 1:
                apply_discovery_batch(retained, batch[:17])
                raise RuntimeError("broker connection dropped during cleanup")
        apply_discovery_batch(retained, batch)
        return True

    def single(conf, topic, payload, retain=False):
        calls.append(("single", {"topic": topic, "payload": payload, "retain": retain}))
        return True

    monkeypatch.setattr(grott_ha, "publish_multiple", multiple_with_partial_delivery)
    monkeypatch.setattr(grott_ha, "publish_single", single)

    assert grott_ha.grottext(
        make_conf(),
        "",
        json.dumps({"device": "PARTIAL1", "values": values}),
    ) == 0
    failed_batch = list(cleanup_batches(calls)[0])
    assert len(state_messages(calls)) == 1
    assert standard_topics("PARTIAL1").issubset(retained)

    assert grott_ha.grottext(
        make_conf(),
        "",
        json.dumps({"device": "PARTIAL1", "values": values}),
    ) == 0
    assert cleanup_batches(calls)[1] == failed_batch
    assert len(state_messages(calls)) == 2
    assert set(retained) == standard_topics("PARTIAL1")


def test_cleanup_preserves_unrelated_retained_topics_byte_for_byte(monkeypatch):
    retained = {}
    calls = capture_publishers(monkeypatch)
    values = decoded_values(mod_layout())

    assert grott_ha.grottext(
        make_conf(profile="all"),
        "",
        json.dumps({"device": "TARGET1", "values": values}),
    ) == 0
    apply_retained(retained, calls)

    unrelated = {
        "homeassistant/sensor/grott/OTHER1_custom/config": '{"owner":"other-device"}',
        "homeassistant/binary_sensor/another_integration/status/config": b"unchanged-bytes",
    }
    retained.update(unrelated)
    calls.clear()

    assert grott_ha.grottext(
        make_conf(),
        "",
        json.dumps({"device": "TARGET1", "values": values}),
    ) == 0
    apply_retained(retained, calls)

    assert {topic: retained[topic] for topic in unrelated} == unrelated
    target_topics = {topic for topic in retained if "/TARGET1_" in topic}
    assert target_topics == standard_topics("TARGET1")


def test_retained_profile_and_layout_switch_matrix_converges(monkeypatch):
    retained = {}
    calls = capture_publishers(monkeypatch)
    device = "SWITCH1"

    assert grott_ha.grottext(
        make_conf(),
        "",
        json.dumps({"device": device, "values": decoded_values(mod_layout())}),
    ) == 0
    apply_retained(retained, calls)
    assert set(retained) == standard_topics(device)

    calls.clear()
    assert grott_ha.grottext(
        make_conf(profile="all"),
        "",
        json.dumps({"device": device, "values": decoded_values(mod_layout())}),
    ) == 0
    apply_retained(retained, calls)
    assert len(retained) == 171

    grott_ha.MqttStateHandler._MqttStateHandler__pv_config = {}
    calls.clear()
    assert grott_ha.grottext(
        make_conf(layout_name="T06NNNNX", profile="all"),
        "",
        json.dumps({"device": device, "values": decoded_values(generic_layout())}),
    ) == 0
    apply_retained(retained, calls)
    assert set(retained) == standard_topics(device)

    calls.clear()
    assert grott_ha.grottext(
        make_conf(profile="all"),
        "",
        json.dumps({"device": device, "values": decoded_values(mod_layout())}),
    ) == 0
    apply_retained(retained, calls)
    assert len(retained) == 171


def test_includeall_all_profile_is_complete_and_default_cleans_it(monkeypatch):
    retained = {}
    calls = capture_publishers(monkeypatch)
    layout = mod_layout()
    excluded_keys = {
        grott_ha.normalize_key(key)
        for key, entry in layout.items()
        if isinstance(entry, dict)
        and "length" in entry
        and entry.get("incl") == "no"
    }

    assert len(excluded_keys) == 34
    assert grott_ha.grottext(
        make_conf(profile="all"),
        "",
        json.dumps(
            {"device": "INCLUDEALL1", "values": decoded_values(layout)}
        ),
    ) == 0
    apply_retained(retained, calls)
    assert len(retained) == 171

    calls.clear()
    assert grott_ha.grottext(
        make_conf(profile="all", includeall=True),
        "",
        json.dumps(
            {
                "device": "INCLUDEALL1",
                "values": decoded_values(layout, include_excluded=True),
            }
        ),
    ) == 0
    apply_retained(retained, calls)

    assert len(retained) == 205
    assert len(desired_batches(calls)[0]) == 205
    assert {
        f"homeassistant/sensor/grott/INCLUDEALL1_{key}/config"
        for key in excluded_keys
    }.issubset(retained)

    calls.clear()
    assert grott_ha.grottext(
        make_conf(),
        "",
        json.dumps(
            {"device": "INCLUDEALL1", "values": decoded_values(layout)}
        ),
    ) == 0
    apply_retained(retained, calls)

    assert set(retained) == standard_topics("INCLUDEALL1")


def test_generic_includeall_all_profile_has_36_configs(monkeypatch):
    calls = capture_publishers(monkeypatch)
    layout = generic_layout()

    assert grott_ha.grottext(
        make_conf(layout_name="T06NNNNX", profile="all", includeall=True),
        "",
        json.dumps(
            {
                "device": "GENINCLUDEALL1",
                "values": decoded_values(layout, include_excluded=True),
            }
        ),
    ) == 0

    assert len(desired_batches(calls)[0]) == 36
