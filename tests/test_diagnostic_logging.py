import importlib.util
import json
import os
from types import SimpleNamespace

import grottdata
import grottproxy


PLUGIN_PATH = os.path.join(
    os.path.dirname(__file__), "..", "examples", "Home Assistent", "grott_ha.py"
)
spec = importlib.util.spec_from_file_location("grott_ha", PLUGIN_PATH)
grott_ha = importlib.util.module_from_spec(spec)
spec.loader.exec_module(grott_ha)


class FakeSocket:
    def __init__(self):
        self.sent = []

    def send(self, data):
        self.sent.append(data)


def make_proxy_conf(**overrides):
    conf = {
        "blockcmd": False,
        "recwl": {"0104", "0150"},
        "verbose": True,
        "minrecl": 100,
        "noipf": False,
    }
    conf.update(overrides)
    return SimpleNamespace(**conf)


def make_packet(record_type_hex: str, size: int = 16, protocol: int = 2):
    data = bytearray(size)
    data[3] = protocol
    data[6] = int(record_type_hex[0:2], 16)
    data[7] = int(record_type_hex[2:4], 16)
    return bytes(data)


def make_proxy_with_packet(data):
    proxy = object.__new__(grottproxy.Proxy)
    source = FakeSocket()
    target = FakeSocket()
    proxy.data = data
    proxy.s = source
    proxy.channel = {source: target}
    return proxy, source, target


def make_publish_conf(**overrides):
    conf = {
        "nomqtt": True,
        "extension": True,
        "extname": "grottext.ha",
        "mqttip": "localhost",
        "mqttport": 1883,
        "mqtttopic": "energy/growatt",
        "mqttretain": False,
    }
    conf.update(overrides)
    return SimpleNamespace(**conf)


def make_ha_conf(**overrides):
    conf = {
        "layout": "T06NNNNXSPA",
        "recorddict": {
            "T06NNNNXSPA": {
                "pvpowerout": {"value": 258, "length": 4, "type": "num", "divide": 10},
            }
        },
        "extvar": {"ha_mqtt_host": "localhost", "ha_mqtt_port": 1883},
        "verbose": True,
    }
    conf.update(overrides)
    return SimpleNamespace(**conf)


def test_blocked_record_log_explains_forwarding_and_publish(monkeypatch, capsys):
    monkeypatch.setattr(grottproxy, "validate_record", lambda _data: 0)
    proxy, _source, target = make_proxy_with_packet(make_packet("0141"))

    proxy.on_recv(make_proxy_conf(blockcmd=True))

    output = capsys.readouterr().out
    assert "Record blocked:  0141" in output
    assert "blocked before forward to Growatt and before local publish" in output
    assert target.sent == []


def test_short_record_log_reports_length_and_threshold(monkeypatch, capsys):
    monkeypatch.setattr(grottproxy, "validate_record", lambda _data: 0)
    proxy, _source, target = make_proxy_with_packet(make_packet("0104", size=94))

    proxy.on_recv(make_proxy_conf(minrecl=100))

    output = capsys.readouterr().out
    assert "record 0104 forwarded to Growatt but not processed locally" in output
    assert "len=94" in output
    assert "minrecl=100" in output
    assert target.sent == [make_packet("0104", size=94)]


def test_publish_path_summary_identifies_native_and_extension_modes():
    summary = grottdata.describe_publish_path(make_publish_conf())
    assert "native MQTT disabled" in summary
    assert "extension enabled: grottext.ha" in summary


def test_ha_plugin_logs_discovery_and_state_publish(monkeypatch, capsys):
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
    message = {"device": "INV123", "buffered": "no", "values": {"pvpowerout": 1200}}

    assert grott_ha.grottext(make_ha_conf(), "", json.dumps(message)) == 0

    output = capsys.readouterr().out
    assert "published 2 Home Assistant discovery topics for INV123" in output
    assert "published Home Assistant state topic for INV123" in output
