import copy
import json
from pathlib import Path
from types import SimpleNamespace

import grottdata
import grottproxy
import grottext.ha as ha_extension
import pytest
from grottconf import Conf
from grottdata import procdata


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "layout_samples.json"
PROTOCOL_06_OUTPUT_PATH = (
    Path(__file__).parent / "fixtures" / "protocol06_expected_output.json"
)


def make_protocol_06_conf():
    conf = Conf.__new__(Conf)
    conf.verbose = False
    conf.set_reclayouts()
    conf.compat = False
    conf.layout_strict = False
    conf.invtype = "default"
    conf.invtypemap = {}
    conf.includeall = False
    conf.layout_auto_family = False
    conf.layout_min_score = 20
    conf.gtime = "auto"
    conf.inverterid = "TEST-INVERTER"
    conf.nomqtt = False
    conf.mqtttopic = "energy/growatt"
    conf.mqttmtopic = False
    conf.mqttmtopicname = "energy/meter"
    conf.mqttinverterintopic = False
    conf.mqttretain = False
    conf.mqttip = "localhost"
    conf.mqttport = 1883
    conf.pubauth = None
    conf.pvoutput = False
    conf.influx = False
    conf.extension = True
    conf.extname = "grottext.ha"
    return conf


def load_family_sample(family):
    with FIXTURE_PATH.open("r", encoding="utf-8") as handle:
        samples = json.load(handle)
    return next(sample for sample in samples if sample["family"] == family)


def build_packet(sample, inverter_serial):
    packet = bytearray(sample["ndata"])
    packet[:8] = bytes.fromhex(sample["header"])

    for field in sample["fields"]:
        start = field["offset"] // 2
        length = field["length"]
        value = inverter_serial if field["offset"] == 76 else field["value"]
        if field["type"] == "text":
            raw = str(value).encode("ascii").ljust(length, b"\x00")[:length]
        elif field["type"] == "numx":
            raw = int(value).to_bytes(length, "big", signed=True)
        else:
            raw = int(value).to_bytes(length, "big", signed=False)
        packet[start : start + length] = raw

    return bytes(packet)


def make_conf(sample, inverter_serial, family):
    layouts = copy.deepcopy(sample["layouts"])
    for layout in layouts.values():
        layout["decrypt"] = {"value": "False"}

    return SimpleNamespace(
        verbose=False,
        compat=False,
        layout_strict=False,
        invtype="default",
        invtypemap={inverter_serial: family},
        includeall=False,
        layout_auto_family=True,
        layout_min_score=20,
        recorddict=layouts,
        inverterid="TEST-INVERTER",
        nomqtt=True,
        pvoutput=False,
        influx=False,
        extension=False,
    )


def test_procdata_applies_mapped_sph_family_exactly_once():
    sample = load_family_sample("sph")
    inverter_serial = "SPH0000001"
    conf = make_conf(sample, inverter_serial, "sph")

    procdata(conf, build_packet(sample, inverter_serial))

    assert conf.layout == "T06NNNNXSPH"
    assert conf.layout != "T06NNNNXSPHSPH"


def test_procdata_applies_null_padded_mapped_sph_family_exactly_once():
    sample = load_family_sample("sph")
    inverter_serial = "SPH000001"
    conf = make_conf(sample, inverter_serial, "sph")

    packet = build_packet(sample, inverter_serial)
    assert packet[38:48] == b"SPH000001\x00"

    procdata(conf, packet)

    assert conf.layout == "T06NNNNXSPH"
    assert conf.layout != "T06NNNNXSPHSPH"


def test_procdata_applies_mapped_tl3_family_exactly_once():
    sample = load_family_sample("tl3")
    inverter_serial = "TL30000001"
    conf = make_conf(sample, inverter_serial, "tl3")

    procdata(conf, build_packet(sample, inverter_serial))

    assert conf.layout == "T06NNNNXTL3"
    assert conf.layout != "T06NNNNXTL3TL3"


def test_procdata_keeps_generic_layout_for_unmapped_default_inverter():
    sample = load_family_sample("sph")
    inverter_serial = "GEN0000001"
    conf = make_conf(sample, inverter_serial, "sph")
    conf.invtypemap = {}
    conf.layout_auto_family = False

    procdata(conf, build_packet(sample, inverter_serial))

    assert conf.layout == "T06NNNNX"


def test_procdata_preserves_strict_configured_family_selection():
    sample = load_family_sample("sph")
    inverter_serial = "SPH0000001"
    conf = make_conf(sample, inverter_serial, "sph")
    conf.invtype = "sph"
    conf.invtypemap = {}
    conf.layout_strict = True

    procdata(conf, build_packet(sample, inverter_serial))

    assert conf.layout == "T06NNNNXSPH"


def test_procdata_does_not_apply_inverter_family_to_smart_meter_layout():
    packet = bytearray(100)
    packet[:8] = bytes.fromhex("0000000600000120")
    packet[8:18] = b"MTR0000001"
    packet[38:40] = (2300).to_bytes(2, "big")
    conf = SimpleNamespace(
        verbose=False,
        compat=False,
        layout_strict=False,
        invtype="default",
        invtypemap={"MTR0000001": "sph"},
        includeall=False,
        layout_auto_family=True,
        layout_min_score=20,
        recorddict={
            "T060120": {
                "decrypt": {"value": "False"},
                "device": {"value": "SMART-METER"},
                "datalogserial": {"value": 16, "length": 10, "type": "text"},
                "voltage_l1": {
                    "value": 76,
                    "length": 2,
                    "type": "num",
                    "divide": 10,
                },
            }
        },
        inverterid="TEST-INVERTER",
        nomqtt=True,
        pvoutput=False,
        influx=False,
        extension=False,
    )

    procdata(conf, bytes(packet))

    assert conf.layout == "T060120"


def test_sanitized_protocol_06_capture_matches_golden_procdata_output(
    monkeypatch,
    tmp_path,
    sanitized_protocol_06_capture,
    decrypt_protocol_06_capture,
):
    monkeypatch.chdir(tmp_path)
    conf = make_protocol_06_conf()

    mqtt_calls = []
    ha_calls = []

    def capture_mqtt(topic, payload, **kwargs):
        mqtt_calls.append((topic, payload, kwargs))

    def capture_ha(extension_conf, result_string, jsonmsg):
        ha_calls.append((extension_conf, result_string, jsonmsg))
        return True

    monkeypatch.setattr(grottdata.publish, "single", capture_mqtt)
    monkeypatch.setattr(ha_extension, "grottext", capture_ha)

    frame = sanitized_protocol_06_capture
    expected = json.loads(PROTOCOL_06_OUTPUT_PATH.read_text(encoding="utf-8"))

    procdata(conf, frame)

    assert conf.layout == "T06NNNN"
    assert len(mqtt_calls) == 1
    mqtt_topic, mqtt_payload, _mqtt_options = mqtt_calls[0]
    assert mqtt_topic == "energy/growatt"
    assert json.loads(mqtt_payload) == expected

    assert len(ha_calls) == 1
    extension_conf, result_string, ha_payload = ha_calls[0]
    assert extension_conf is conf
    assert ha_payload == mqtt_payload
    assert json.loads(ha_payload) == expected
    assert bytes.fromhex(result_string)[:-2] == decrypt_protocol_06_capture(frame)


@pytest.mark.parametrize("influx2", [False, True])
def test_influx_write_boundary_raises_typed_error_without_secret(
    influx2,
):
    secret = "SENTINEL_INFLUX_BOUNDARY_SECRET"

    class FailingV1:
        def write_points(self, _points):
            raise RuntimeError(secret)

    class FailingV2:
        def write(self, _bucket, _org, _points):
            raise RuntimeError(secret)

    conf = SimpleNamespace(
        influx2=influx2,
        influxclient=FailingV1(),
        ifwrite_api=FailingV2(),
        ifbucket="bucket",
        iforg="org",
    )

    assert hasattr(grottdata, "InfluxSinkError")
    assert hasattr(grottdata, "write_influx_points")
    with pytest.raises(grottdata.InfluxSinkError) as error:
        grottdata.write_influx_points(conf, [{"measurement": "test"}])

    assert secret not in str(error.value)


@pytest.mark.parametrize("influx2", [False, True])
def test_influx_write_failure_is_contained_and_extension_still_runs(
    monkeypatch,
    tmp_path,
    capsys,
    sanitized_protocol_06_capture,
    influx2,
):
    secret = "SENTINEL_INFLUX_WRITE_SECRET"

    class FailingV1:
        def write_points(self, _points):
            raise RuntimeError(secret)

    class FailingV2:
        def write(self, _bucket, _org, _points):
            raise RuntimeError(secret)

    monkeypatch.chdir(tmp_path)
    conf = make_protocol_06_conf()
    conf.nomqtt = True
    conf.influx = True
    conf.influx2 = influx2
    conf.tmzone = "local"
    conf.influxclient = FailingV1()
    conf.ifwrite_api = FailingV2()
    conf.ifbucket = "bucket"
    conf.iforg = "org"
    conf.blockcmd = False
    conf.minrecl = 0
    extension_calls = []
    monkeypatch.setattr(
        ha_extension,
        "grottext",
        lambda *args: extension_calls.append(args) or True,
    )

    class RecordingSocket:
        def __init__(self):
            self.sent = []

        def sendall(self, data):
            self.sent.append(data)

    source = RecordingSocket()
    upstream = RecordingSocket()
    proxy = object.__new__(grottproxy.Proxy)
    proxy.channel = {source: upstream, upstream: source}
    proxy.s = source
    proxy.data = sanitized_protocol_06_capture

    proxy.on_recv(conf)

    output = capsys.readouterr().out
    assert upstream.sent == [sanitized_protocol_06_capture]
    assert secret not in output
    assert "InfluxSinkError" in output
    assert len(extension_calls) == 1
