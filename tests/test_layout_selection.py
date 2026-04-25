from types import SimpleNamespace

from grottlayout import (
    build_candidate_layouts,
    generic_layout_name,
    LayoutSelection,
    normalize_key,
    parse_layout_values,
    select_layout,
)
from grottdata import layout_selection_is_usable


def make_conf(invtype="default", strict=False):
    return SimpleNamespace(
        invtype=invtype,
        invtypemap={},
        includeall=False,
        layout_strict=strict,
        layout_auto_family=True,
        layout_min_score=20,
        recorddict={
            "T06NNNNX": {
                "decrypt": {"value": "True"},
                "datalogserial": {
                    "value": 16,
                    "length": 10,
                    "type": "text",
                    "incl": "yes",
                },
                "pvserial": {"value": 76, "length": 10, "type": "text"},
                "date": {"value": 136, "divide": 10},
                "pvstatus": {"value": 158, "length": 2, "type": "num"},
                "pvpowerin": {"value": 162, "length": 4, "type": "num", "divide": 10},
                "pvpowerout": {
                    "value": 250,
                    "length": 4,
                    "type": "numx",
                    "divide": 10,
                },
                "pvfrequentie": {
                    "value": 258,
                    "length": 2,
                    "type": "num",
                    "divide": 100,
                },
                "pvgridvoltage": {
                    "value": 262,
                    "length": 2,
                    "type": "num",
                    "divide": 10,
                },
                "pvenergytoday": {
                    "value": 354,
                    "length": 4,
                    "type": "num",
                    "divide": 10,
                },
            },
            "T06NNNNXSPH": {
                "decrypt": {"value": "True"},
                "datalogserial": {
                    "value": 16,
                    "length": 10,
                    "type": "text",
                    "incl": "yes",
                },
                "pvserial": {"value": 76, "length": 10, "type": "text"},
                "date": {"value": 136, "divide": 10},
                "pvstatus": {"value": 158, "length": 2, "type": "num"},
                "pvpowerin": {"value": 162, "length": 4, "type": "num", "divide": 10},
                "pvpowerout": {
                    "value": 298,
                    "length": 4,
                    "type": "numx",
                    "divide": 10,
                },
                "pvfrequentie": {
                    "value": 306,
                    "length": 2,
                    "type": "num",
                    "divide": 100,
                },
                "pvgridvoltage": {
                    "value": 310,
                    "length": 2,
                    "type": "num",
                    "divide": 10,
                },
                "pvenergytoday": {
                    "value": 370,
                    "length": 4,
                    "type": "num",
                    "divide": 10,
                },
                "vbat": {"value": 718, "length": 2, "type": "num", "divide": 10},
                "SOC": {"value": 722, "length": 2, "type": "num", "divide": 1},
                "batterytype": {"value": 1650, "length": 2, "type": "num", "divide": 1},
            },
        },
    )


def blank_payload(hex_length=1700):
    return bytearray.fromhex("00" * (hex_length // 2))


def put_text(payload, offset, value, length=10):
    raw = value.encode("ascii").ljust(length, b"\x00")[:length]
    payload[offset // 2 : offset // 2 + length] = raw


def put_num(payload, offset, value, length=2):
    payload[offset // 2 : offset // 2 + length] = int(value).to_bytes(
        length, "big", signed=False
    )


def put_numx(payload, offset, value, length=4):
    payload[offset // 2 : offset // 2 + length] = int(value).to_bytes(
        length, "big", signed=True
    )


def sample_decrypted_payload():
    payload = blank_payload()
    put_text(payload, 16, "DL12345678")
    put_text(payload, 76, "INV1234567")
    payload[136 // 2 : 142 // 2] = bytes([26, 4, 25, 10, 15, 0])
    put_num(payload, 158, 1)
    put_num(payload, 162, 14739, length=4)
    put_numx(payload, 250, 14445, length=4)
    put_num(payload, 258, 5000)
    put_num(payload, 262, 2510)
    put_num(payload, 354, 31, length=4)
    return payload.hex()


def test_0103_uses_generic_extended_fallback():
    assert generic_layout_name("T060103X", "01", "03") == "T06NNNNX"


def test_candidates_include_generic_for_0103():
    conf = make_conf()
    candidates = build_candidate_layouts(
        conf=conf,
        header="0000000600000103",
        ndata=585,
        is_smart_meter=False,
        inverter_serial="INV1234567",
    )
    assert "T06NNNNX" in [candidate.layout for candidate in candidates]


def test_forced_sph_is_rejected_when_generic_is_more_plausible():
    conf = make_conf(invtype="sph")
    selected = select_layout(
        conf=conf,
        header="0000000600000104",
        ndata=585,
        is_smart_meter=False,
        result_string=sample_decrypted_payload(),
        current_layout="T06NNNNX",
    )
    assert selected.layout == "T06NNNNX"
    assert selected.rejected["T06NNNNXSPH"]


def test_strict_sph_keeps_legacy_forced_layout():
    conf = make_conf(invtype="sph", strict=True)
    selected = select_layout(
        conf=conf,
        header="0000000600000104",
        ndata=585,
        is_smart_meter=False,
        result_string=sample_decrypted_payload(),
        current_layout="T06NNNNX",
    )
    assert selected.layout == "T06NNNNXSPH"


def test_parse_layout_values_skips_bad_optional_key():
    conf = make_conf()
    conf.recorddict["T06NNNNX"]["badtext"] = {"value": 2000, "length": 4, "type": "text"}
    values, errors = parse_layout_values(conf.recorddict["T06NNNNX"], sample_decrypted_payload())
    assert values["pvserial"] == "INV1234567"
    assert "badtext" in errors


def test_normalize_key_removes_illegal_topic_characters():
    assert normalize_key("pactogrids ") == "pactogrids"
    assert normalize_key("pactogrid t") == "pactogrid_t"
    assert normalize_key("#battemp") == "battemp"


def test_low_score_inverter_layout_selection_is_not_usable():
    conf = make_conf()
    selection = LayoutSelection("T06NNNNX", 11)

    assert layout_selection_is_usable(conf, selection, is_smart_meter=False) is False


def test_low_score_smart_meter_layout_selection_remains_usable():
    conf = make_conf()
    selection = LayoutSelection("T05201b", 11)

    assert layout_selection_is_usable(conf, selection, is_smart_meter=True) is True
