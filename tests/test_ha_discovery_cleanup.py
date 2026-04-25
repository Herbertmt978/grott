from argparse import Namespace

import pytest

from tools import ha_discovery_cleanup
from tools.ha_discovery_cleanup import cleanup_plan, discovery_pattern, extract_attribute, parse_keep


def test_discovery_pattern_targets_one_grott_device():
    assert (
        discovery_pattern("homeassistant/sensor/grott", "DL12345678")
        == "homeassistant/sensor/grott/DL12345678_+/config"
    )


def test_extract_attribute_from_valid_discovery_topic():
    assert (
        extract_attribute("homeassistant/sensor/grott/DL12345678_pvpowerout/config", "DL12345678")
        == "pvpowerout"
    )


def test_extract_attribute_ignores_other_devices_and_nested_topics():
    assert extract_attribute("homeassistant/sensor/grott/OTHER_pvpowerout/config", "DL12345678") is None
    assert (
        extract_attribute("homeassistant/sensor/grott/DL12345678_nested/value/config", "DL12345678")
        is None
    )


def test_cleanup_plan_deduplicates_and_keeps_selected_attributes():
    topics = [
        "homeassistant/sensor/grott/DL12345678_pvpowerout/config",
        "homeassistant/sensor/grott/DL12345678_pvpowerout/config",
        "homeassistant/sensor/grott/DL12345678_SOC/config",
        "homeassistant/sensor/grott/DL12345678_bad_legacy/config",
    ]
    targets = cleanup_plan(topics, "DL12345678", keep={"pvpowerout", "SOC"})
    assert [target.attribute for target in targets] == ["bad_legacy"]


def test_cleanup_plan_can_clear_all_for_a_device():
    topics = [
        "homeassistant/sensor/grott/DL12345678_pvpowerout/config",
        "homeassistant/sensor/grott/DL12345678_SOC/config",
        "homeassistant/sensor/grott/OTHER_SOC/config",
    ]
    targets = cleanup_plan(topics, "DL12345678", clear_all=True)
    assert [target.attribute for target in targets] == ["SOC", "pvpowerout"]


def test_parse_keep_trims_empty_values():
    assert parse_keep("pvpowerout, SOC,,") == {"pvpowerout", "SOC"}


class ConnackFailureClient:
    def __init__(self):
        self.on_connect = None
        self.on_message = None
        self.loop_stopped = False
        self.disconnected = False

    def connect(self, _host, _port, keepalive=30):
        return 0

    def loop_start(self):
        self.on_connect(self, None, None, 5)

    def loop_stop(self):
        self.loop_stopped = True

    def disconnect(self):
        self.disconnected = True


def test_discover_topics_surfaces_connack_failure(monkeypatch):
    client = ConnackFailureClient()
    monkeypatch.setattr(ha_discovery_cleanup, "mqtt_client", lambda _username, _password: client)
    args = Namespace(
        host="mqtt.local",
        port=1883,
        username=None,
        password=None,
        prefix="homeassistant/sensor/grott",
        device="DL12345678",
        timeout=0.1,
    )

    with pytest.raises(SystemExit, match="MQTT connect failed with rc=5"):
        ha_discovery_cleanup.discover_topics(args)

    assert client.loop_stopped is True
    assert client.disconnected is True
