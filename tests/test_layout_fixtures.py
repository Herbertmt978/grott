import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from grottlayout import parse_layout_values, select_layout


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "layout_samples.json"


def build_payload_hex(fields):
    max_byte = max((field["offset"] // 2) + field["length"] for field in fields)
    payload = bytearray(max_byte + 8)
    for field in fields:
        start = field["offset"] // 2
        length = field["length"]
        if field["type"] == "text":
            raw = str(field["value"]).encode("ascii").ljust(length, b"\x00")[:length]
        elif field["type"] == "numx":
            raw = int(field["value"]).to_bytes(length, "big", signed=True)
        else:
            raw = int(field["value"]).to_bytes(length, "big", signed=False)
        payload[start : start + length] = raw
    return payload.hex()


def load_samples():
    with FIXTURE_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


@pytest.mark.parametrize("sample", load_samples(), ids=lambda sample: sample["name"])
def test_sanitized_family_fixtures_select_expected_layout(sample):
    conf = SimpleNamespace(
        invtype=sample["family"],
        invtypemap={},
        includeall=False,
        layout_strict=False,
        layout_auto_family=True,
        layout_min_score=20,
        recorddict=sample["layouts"],
    )
    payload_hex = build_payload_hex(sample["fields"])
    selection = select_layout(
        conf=conf,
        header=sample["header"],
        ndata=sample["ndata"],
        is_smart_meter=False,
        result_string=payload_hex,
        current_layout=sample["current_layout"],
    )
    assert selection.layout == sample["expected_layout"]

    values, errors = parse_layout_values(sample["layouts"][selection.layout], payload_hex)
    assert not errors
    for key, value in sample["expected_values"].items():
        assert values[key] == value
