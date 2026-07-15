import importlib.util
import json
import os
from types import SimpleNamespace


PLUGIN_PATH = os.path.join(
    os.path.dirname(__file__), "..", "examples", "Home Assistent", "grott_ha.py"
)
spec = importlib.util.spec_from_file_location("grott_ha", PLUGIN_PATH)
grott_ha = importlib.util.module_from_spec(spec)
spec.loader.exec_module(grott_ha)


def make_conf():
    return SimpleNamespace(
        layout="T06NNNNXSPA",
        recorddict={
            "T06NNNNXSPA": {
                "pactogrids ": {"value": 258, "length": 4, "type": "num", "divide": 10},
                "battemp ": {"value": 318, "length": 2, "type": "num", "divide": 10},
                "grott_last_push": {"value": 0, "type": "text"},
            }
        },
        extvar={"ha_mqtt_host": "localhost", "ha_mqtt_port": 1883},
        verbose=False,
    )


def test_make_payload_uses_sanitized_unique_id_and_template():
    payload = grott_ha.make_payload(make_conf(), "INV123", "pactogrids ", "pactogrids ")
    assert payload["unique_id"] == "grott_INV123_pactogrids"
    assert payload["value_template"] == "{{ value_json.pactogrids | float / 10 }}"
    assert "expire_after" not in payload


def test_make_payload_keeps_expire_after_for_grott_last_push():
    payload = grott_ha.make_payload(
        make_conf(), "INV123", "grott_last_push", "grott_last_push"
    )

    assert payload["expire_after"] == 900


def test_normalize_values_preserves_last_value_for_sanitized_key():
    values = grott_ha.normalize_values({"pactogrids ": 12, "pactogrids": 13, "battemp ": 22})
    assert values["pactogrids"] == 13
    assert values["battemp"] == 22


def test_config_topics_are_legal(monkeypatch):
    sent = []

    def fake_multiple(conf, msgs):
        sent.extend(msgs)
        return True

    def fake_single(conf, topic, payload, retain=False):
        sent.append({"topic": topic, "payload": payload, "retain": retain})
        return True

    monkeypatch.setattr(grott_ha, "publish_multiple", fake_multiple)
    monkeypatch.setattr(grott_ha, "publish_single", fake_single)
    grott_ha.MqttStateHandler._MqttStateHandler__pv_config = {}
    message = {"device": "INV123", "buffered": "no", "values": {"pactogrids ": 12}}

    assert grott_ha.grottext(make_conf(), "", json.dumps(message)) == 0
    topics = [item["topic"] for item in sent]
    assert "homeassistant/sensor/grott/INV123_pactogrids/config" in topics
    assert all(" " not in topic for topic in topics)
    state = next(
        item for item in sent if item["topic"] == "homeassistant/grott/INV123/state"
    )
    state_values = json.loads(state["payload"])
    assert state_values["pactogrids"] == 12
    assert "pactogrids " not in state_values
