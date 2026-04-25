# Grott Auto Layout HA Docker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a guarded Grott fork release that keeps proxy telemetry working, rejects obviously wrong forced layouts, fixes extended `0103` fallback, sanitizes Home Assistant discovery, and ships one Docker/HA add-on path.

**Architecture:** Keep the existing Grott parser and proxy entrypoints, but add a small layout-selection module that can dry-parse candidate layouts before `grottdata.py` publishes values. Keep HA discovery in the existing plugin file, but normalize keys before topics, unique IDs, and state payloads are generated. Packaging stays thin: Docker and the HA add-on both run the same source tree.

**Tech Stack:** Python 3.11-compatible runtime, existing `paho-mqtt`/Influx/PVOutput dependencies, `pytest` for regression tests, Docker, Home Assistant add-on metadata.

---

## File Structure

- Create `grottlayout.py`: layout name generation, candidate selection, dry-run field parsing, key normalization, and simple plausibility scoring.
- Modify `grottdata.py`: call the layout selector, allow `0103` generic fallback, skip bad optional keys instead of aborting the whole packet, and emit normalized JSON keys.
- Modify `grottconf.py`: add `layout_strict`, `layout_auto_family`, and `layout_min_score` options with config and environment overrides.
- Modify `examples/Home Assistent/grott_ha.py`: normalize discovery keys and state keys, prevent duplicate last-push config, and optionally clear known stale Grott-owned discovery topics.
- Create `tests/conftest.py`, `tests/test_layout_selection.py`, and `tests/test_ha_discovery.py`: parser and HA discovery regressions.
- Create `requirements.txt`: runtime dependencies used by Docker and HA add-on builds.
- Modify `docker/dockerfile` and `docker/docker-compose.yml`: build from this fork, copy the HA plugin, install dependencies from one file, and document safe proxy defaults.
- Create `addons/grott/Dockerfile`, `addons/grott/config.yaml`, `addons/grott/build.yaml`, and `addons/grott/run.sh`: Home Assistant add-on wrapper around the same Grott runtime.
- Modify `README.md` and `examples/grott.ini`: document the beta fork defaults and migration behavior.

---

### Task 1: Add Layout Selector Tests

**Files:**
- Create: `tests/conftest.py`
- Create: `tests/test_layout_selection.py`
- Test: `tests/test_layout_selection.py`

- [ ] **Step 1: Write failing layout-selection tests**

Create `tests/conftest.py`:

```python
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
```

Create `tests/test_layout_selection.py`:

```python
from types import SimpleNamespace

from grottlayout import (
    build_candidate_layouts,
    generic_layout_name,
    normalize_key,
    parse_layout_values,
    select_layout,
)


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
                "datalogserial": {"value": 16, "length": 10, "type": "text", "incl": "yes"},
                "pvserial": {"value": 76, "length": 10, "type": "text"},
                "date": {"value": 136, "divide": 10},
                "pvstatus": {"value": 158, "length": 2, "type": "num"},
                "pvpowerin": {"value": 162, "length": 4, "type": "num", "divide": 10},
                "pvpowerout": {"value": 250, "length": 4, "type": "numx", "divide": 10},
                "pvfrequentie": {"value": 258, "length": 2, "type": "num", "divide": 100},
                "pvgridvoltage": {"value": 262, "length": 2, "type": "num", "divide": 10},
                "pvenergytoday": {"value": 354, "length": 4, "type": "num", "divide": 10},
            },
            "T06NNNNXSPH": {
                "decrypt": {"value": "True"},
                "datalogserial": {"value": 16, "length": 10, "type": "text", "incl": "yes"},
                "pvserial": {"value": 76, "length": 10, "type": "text"},
                "date": {"value": 136, "divide": 10},
                "pvstatus": {"value": 158, "length": 2, "type": "num"},
                "pvpowerin": {"value": 162, "length": 4, "type": "num", "divide": 10},
                "pvpowerout": {"value": 298, "length": 4, "type": "numx", "divide": 10},
                "pvfrequentie": {"value": 306, "length": 2, "type": "num", "divide": 100},
                "pvgridvoltage": {"value": 310, "length": 2, "type": "num", "divide": 10},
                "pvenergytoday": {"value": 370, "length": 4, "type": "num", "divide": 10},
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
    payload[offset // 2 : offset // 2 + length] = int(value).to_bytes(length, "big", signed=False)


def put_numx(payload, offset, value, length=4):
    payload[offset // 2 : offset // 2 + length] = int(value).to_bytes(length, "big", signed=True)


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
    conf.recorddict["T06NNNNX"]["badtext"] = {"value": 20, "length": 4, "type": "text"}
    values, errors = parse_layout_values(conf.recorddict["T06NNNNX"], sample_decrypted_payload())
    assert values["pvserial"] == "INV1234567"
    assert "badtext" in errors


def test_normalize_key_removes_illegal_topic_characters():
    assert normalize_key("pactogrids ") == "pactogrids"
    assert normalize_key("pactogrid t") == "pactogrid_t"
    assert normalize_key("#battemp") == "battemp"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```powershell
python -m pytest tests/test_layout_selection.py -q
```

Expected: FAIL because `grottlayout.py` does not exist.

- [ ] **Step 3: Commit only if tests are added and failing for the expected import reason**

Run:

```powershell
git add tests/conftest.py tests/test_layout_selection.py
git commit -m "test: cover layout selection edge cases"
```

---

### Task 2: Implement `grottlayout.py`

**Files:**
- Create: `grottlayout.py`
- Test: `tests/test_layout_selection.py`

- [ ] **Step 1: Add the layout helper module**

Create `grottlayout.py` with:

```python
import codecs
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Tuple


GENERIC_FALLBACK_RECORDS = {"03", "04", "50"}
SMART_METER_RECORDS = {"1b", "20", "1e"}
KNOWN_FAMILIES = ("SPH", "SPF", "TL3", "SPA", "MIN", "MOD")


@dataclass(frozen=True)
class LayoutCandidate:
    layout: str
    reason: str
    preferred: bool = False


@dataclass
class LayoutSelection:
    layout: str
    score: int
    values: Dict[str, object] = field(default_factory=dict)
    errors: Dict[str, str] = field(default_factory=dict)
    rejected: Dict[str, List[str]] = field(default_factory=dict)


def normalize_key(key: str) -> str:
    key = str(key).strip().lstrip("#")
    key = re.sub(r"\s+", "_", key)
    key = re.sub(r"[^A-Za-z0-9_]", "_", key)
    key = re.sub(r"_+", "_", key).strip("_")
    return key or "unknown"


def is_printable_serial(value: object) -> bool:
    if not isinstance(value, str):
        return False
    value = value.strip("\x00").strip()
    return bool(value) and all(32 <= ord(ch) <= 126 for ch in value)


def base_layout_name(header: str, ndata: int, is_smart_meter: bool) -> str:
    layout = "T" + header[6:8] + header[12:14] + header[14:16]
    if ndata > 375 and not is_smart_meter:
        layout += "X"
    return layout


def generic_layout_name(layout: str, device_id: str, record_type: str) -> str:
    token = device_id + record_type
    if record_type in GENERIC_FALLBACK_RECORDS and token in layout:
        return layout.replace(token, "NNNN", 1)
    return layout


def append_family(layout: str, family: str) -> str:
    family = str(family or "").upper()
    if not family or family == "DEFAULT":
        return layout
    if layout.upper().endswith(family):
        return layout
    return layout + family


def decode_text(raw_value: str) -> str:
    return codecs.decode(raw_value, "hex").decode("utf-8").strip("\x00")


def parse_layout_values(
    layout_def: Mapping[str, Mapping[str, object]],
    result_string: str,
    includeall: bool = False,
) -> Tuple[Dict[str, object], Dict[str, str]]:
    values: Dict[str, object] = {}
    errors: Dict[str, str] = {}
    logdict: List[str] = []
    if "logstart" in layout_def:
        try:
            start = int(layout_def["logstart"]["value"])
            logdict = bytes.fromhex(result_string[start : len(result_string) - 4]).decode("ASCII").split(",")
        except Exception as exc:
            errors["logstart"] = str(exc)

    for key, spec in layout_def.items():
        if key in ("decrypt", "date", "logstart", "device"):
            continue
        if spec.get("incl") == "no" and not includeall:
            continue
        try:
            keytype = spec.get("type", "num")
            if keytype in ("text", "num", "numx"):
                start = int(spec["value"])
                length = int(spec["length"]) * 2
                raw_value = result_string[start : start + length]
                if len(raw_value) != length:
                    raise ValueError("defined position not present in data")
                if keytype == "text":
                    values[key] = decode_text(raw_value)
                elif keytype == "numx":
                    values[key] = int.from_bytes(bytes.fromhex(raw_value), byteorder="big", signed=True)
                else:
                    values[key] = int(raw_value, 16)
            elif keytype == "log":
                values[key] = logdict[int(spec["pos"]) - 1]
            elif keytype == "logpos":
                candidate = logdict[int(spec["pos"]) - 1]
                values[key] = candidate if float(candidate) > 0 else 0
            elif keytype == "logneg":
                candidate = logdict[int(spec["pos"]) - 1]
                values[key] = candidate if float(candidate) < 0 else 0
        except Exception as exc:
            errors[key] = str(exc)
    return values, errors


def divided(layout_def: Mapping[str, Mapping[str, object]], values: Mapping[str, object], key: str) -> Optional[float]:
    if key not in values or not isinstance(values[key], int):
        return None
    divide = layout_def.get(key, {}).get("divide", 1) or 1
    return values[key] / float(divide)


def score_layout(
    layout: str,
    layout_def: Mapping[str, Mapping[str, object]],
    values: Mapping[str, object],
    errors: Mapping[str, str],
    preferred: bool = False,
) -> Tuple[int, List[str]]:
    score = 0
    reasons: List[str] = []

    for serial_key in ("datalogserial", "pvserial"):
        if serial_key in layout_def:
            if is_printable_serial(values.get(serial_key)):
                score += 15
            else:
                score -= 35
                reasons.append(f"{serial_key} is not printable")

    for key in ("pvpowerin", "pvpowerout", "pv1watt", "pv2watt", "pvgridpower"):
        value = divided(layout_def, values, key)
        if value is None:
            continue
        if -100000 <= value <= 100000:
            score += 5
        else:
            score -= 20
            reasons.append(f"{key} out of range")

    for key in ("pvgridvoltage", "pvgridvoltage2", "pvgridvoltage3", "pv1voltage", "pv2voltage"):
        value = divided(layout_def, values, key)
        if value is None:
            continue
        if value == 0 or 50 <= value <= 1000:
            score += 4
        else:
            score -= 18
            reasons.append(f"{key} out of range")

    freq = divided(layout_def, values, "pvfrequentie")
    if freq is not None:
        if freq == 0 or 45 <= freq <= 65:
            score += 4
        else:
            score -= 18
            reasons.append("pvfrequentie out of range")

    for key in ("pvenergytoday", "pvenergytotal", "epvtotal", "epv1today", "epv2today"):
        value = divided(layout_def, values, key)
        if value is None:
            continue
        if 0 <= value <= 10000000:
            score += 3
        else:
            score -= 20
            reasons.append(f"{key} out of range")

    battery_keys = [key for key in ("SOC", "vbat", "batterytype", "bmsbatteryvolt", "bmsbatterycurr") if key in layout_def]
    if battery_keys:
        zero_battery = 0
        for key in battery_keys:
            value = divided(layout_def, values, key)
            if value == 0:
                zero_battery += 1
            if key.upper() == "SOC" and value is not None:
                if 1 <= value <= 100:
                    score += 8
                else:
                    score -= 20
                    reasons.append("SOC out of range or empty")
        pvout = divided(layout_def, values, "pvpowerout") or 0
        if pvout > 100 and zero_battery >= 2:
            score -= 25
            reasons.append("battery fields empty while PV output is active")

    if layout.endswith("X") or "NNNN" in layout:
        score += 3
    if preferred:
        score += 4
    if errors:
        score -= min(30, len(errors) * 3)
        reasons.append(f"{len(errors)} parse error(s)")
    return score, reasons


def _append_unique(candidates: List[LayoutCandidate], layout: str, reason: str, preferred: bool = False) -> None:
    if layout and all(candidate.layout != layout for candidate in candidates):
        candidates.append(LayoutCandidate(layout=layout, reason=reason, preferred=preferred))


def build_candidate_layouts(
    conf,
    header: str,
    ndata: int,
    is_smart_meter: bool,
    inverter_serial: Optional[str] = None,
) -> List[LayoutCandidate]:
    record_type = header[14:16]
    device_id = header[12:14]
    base = base_layout_name(header, ndata, is_smart_meter)
    generic = generic_layout_name(base, device_id, record_type)
    invtype = str(getattr(conf, "invtype", "default") or "default").upper()
    strict = bool(getattr(conf, "layout_strict", False))

    candidates: List[LayoutCandidate] = []
    if invtype != "DEFAULT" and not is_smart_meter:
        _append_unique(candidates, append_family(generic, invtype), f"configured invtype={invtype}", True)
        _append_unique(candidates, append_family(base, invtype), f"configured exact invtype={invtype}", True)
        if strict:
            return candidates

    invtypemap = getattr(conf, "invtypemap", {}) or {}
    mapped = invtypemap.get(inverter_serial) if inverter_serial else None
    if mapped and not is_smart_meter:
        _append_unique(candidates, append_family(generic, mapped), f"invtypemap={mapped}", True)
        _append_unique(candidates, append_family(base, mapped), f"invtypemap exact={mapped}", True)

    _append_unique(candidates, base, "exact header")
    _append_unique(candidates, generic, "generic fallback")

    if bool(getattr(conf, "layout_auto_family", False)) and not is_smart_meter and not strict:
        for family in KNOWN_FAMILIES:
            _append_unique(candidates, append_family(generic, family), f"auto family {family}")
    return candidates


def extract_inverter_serial(result_string: str) -> Optional[str]:
    try:
        return decode_text(result_string[76:96])
    except Exception:
        return None


def select_layout(conf, header: str, ndata: int, is_smart_meter: bool, result_string: str, current_layout: str) -> LayoutSelection:
    inverter_serial = extract_inverter_serial(result_string)
    candidates = build_candidate_layouts(conf, header, ndata, is_smart_meter, inverter_serial)
    available = getattr(conf, "recorddict", {})
    best: Optional[LayoutSelection] = None
    rejected: Dict[str, List[str]] = {}

    for candidate in candidates:
        layout_def = available.get(candidate.layout)
        if not layout_def:
            rejected[candidate.layout] = ["layout is not defined"]
            continue
        values, errors = parse_layout_values(layout_def, result_string, getattr(conf, "includeall", False))
        score, reasons = score_layout(candidate.layout, layout_def, values, errors, candidate.preferred)
        if reasons:
            rejected[candidate.layout] = reasons
        selection = LayoutSelection(candidate.layout, score, values, errors, rejected.copy())
        if best is None or selection.score > best.score:
            best = selection

    min_score = int(getattr(conf, "layout_min_score", 20))
    if best and (best.score >= min_score or best.layout == current_layout):
        return best
    return LayoutSelection(current_layout, best.score if best else 0, rejected=rejected)
```

- [ ] **Step 2: Run the layout tests**

Run:

```powershell
python -m pytest tests/test_layout_selection.py -q
```

Expected: PASS.

- [ ] **Step 3: Commit the layout selector**

Run:

```powershell
git add grottlayout.py tests/test_layout_selection.py
git commit -m "feat: add guarded layout selector"
```

---

### Task 3: Integrate Layout Selection Into `grottdata.py` and Config

**Files:**
- Modify: `grottdata.py`
- Modify: `grottconf.py`
- Test: `tests/test_layout_selection.py`

- [ ] **Step 1: Add configuration defaults**

In `grottconf.py`, add defaults in `Conf.__init__` near `self.invtype`:

```python
self.layout_strict = False
self.layout_auto_family = True
self.layout_min_score = 20
```

Add `print()` output after `invtypemap`:

```python
print("\tlayout_strict:       \t", self.layout_strict)
print("\tlayout_auto_family:  \t", self.layout_auto_family)
print("\tlayout_min_score:    \t", self.layout_min_score)
```

Add config-file parsing in the `[Generic]` section near `invtype`:

```python
if config.has_option("Generic", "layout_strict"): self.layout_strict = config.getboolean("Generic", "layout_strict")
if config.has_option("Generic", "layout_auto_family"): self.layout_auto_family = config.getboolean("Generic", "layout_auto_family")
if config.has_option("Generic", "layout_min_score"): self.layout_min_score = config.getint("Generic", "layout_min_score")
```

Add environment parsing near `ginvtype`:

```python
if os.getenv('glayoutstrict') != None : self.layout_strict = self.getenv('glayoutstrict')
if os.getenv('glayoutautofamily') != None : self.layout_auto_family = self.getenv('glayoutautofamily')
if os.getenv('glayoutminscore') != None : self.layout_min_score = int(self.getenv('glayoutminscore'))
```

After the existing boolean conversions, add:

```python
self.layout_strict = str2bool(self.layout_strict)
self.layout_auto_family = str2bool(self.layout_auto_family)
```

- [ ] **Step 2: Use selector and `0103` fallback in `grottdata.py`**

Add imports near the current imports:

```python
from grottlayout import (
    base_layout_name,
    generic_layout_name,
    normalize_key,
    select_layout,
)
```

Replace the automatic layout block at `grottdata.py:99-128` with:

```python
layout = base_layout_name(header, ndata, is_smart_meter)

if header[14:16] == "50":
    buffered = "yes"
else:
    buffered = "no"

if conf.layout_strict and (conf.invtype != "default") and not is_smart_meter:
    layout = layout + conf.invtype.upper()

if conf.verbose:
    print("\t - " + "layout   : ", layout)
try:
    test = conf.recorddict[layout]
except:
    if conf.verbose:
        print("\t - " + "no matching record layout found, try generic")
    layout = generic_layout_name(layout, header[12:14], header[14:16])
    try:
        test = conf.recorddict[layout]
    except:
        if conf.verbose:
            print("\t - " + "no matching record layout found, standard processing performed")
        layout = "none"
        novalidrec = True
```

After the decrypted `result_string` is logged and before the `ndata < 12` check, add:

```python
if conf.compat is False and novalidrec is False:
    selection = select_layout(conf, header, ndata, is_smart_meter, result_string, layout)
    if conf.verbose:
        print("\t - Layout selected: ", selection.layout, "score:", selection.score)
        for rejected_layout, reasons in selection.rejected.items():
            if rejected_layout != selection.layout:
                print("\t\t - rejected", rejected_layout, ":", "; ".join(reasons))
    layout = selection.layout
    conf.layout = layout
```

Change the keyword parse exception at `grottdata.py:267-269` to skip the key:

```python
except Exception as error:
    if conf.verbose:
        print("\t - grottdata - error in keyword processing : ", keyword, "skipped:", error)
    continue
```

Change JSON value emission at `grottdata.py:455-464` to normalize keys:

```python
for key in definedkey:
    jsonobj["values"][normalize_key(key)] = definedkey[key]
```

- [ ] **Step 3: Run parser tests**

Run:

```powershell
python -m pytest tests/test_layout_selection.py -q
```

Expected: PASS.

- [ ] **Step 4: Commit parser integration**

Run:

```powershell
git add grottdata.py grottconf.py
git commit -m "fix: select safe layouts before publishing values"
```

---

### Task 4: Fix Home Assistant Discovery Key Hygiene

**Files:**
- Modify: `examples/Home Assistent/grott_ha.py`
- Create: `tests/test_ha_discovery.py`
- Test: `tests/test_ha_discovery.py`

- [ ] **Step 1: Write failing HA discovery tests**

Create `tests/test_ha_discovery.py`:

```python
import importlib.util
import json
import os
from types import SimpleNamespace


PLUGIN_PATH = os.path.join(os.path.dirname(__file__), "..", "examples", "Home Assistent", "grott_ha.py")
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

    assert grott_ha.grottext(make_conf(), "", json.dumps(message)) is None
    topics = [item["topic"] for item in sent]
    assert "homeassistant/sensor/grott/INV123_pactogrids/config" in topics
    assert all(" " not in topic for topic in topics)
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python -m pytest tests/test_ha_discovery.py -q
```

Expected: FAIL because `normalize_values` does not exist and payloads use raw keys.

- [ ] **Step 3: Implement HA key normalization**

In `examples/Home Assistent/grott_ha.py`, add imports:

```python
import re
```

Add helper functions after `mapping`:

```python
def normalize_key(key: str) -> str:
    key = str(key).strip().lstrip("#")
    key = re.sub(r"\s+", "_", key)
    key = re.sub(r"[^A-Za-z0-9_]", "_", key)
    key = re.sub(r"_+", "_", key).strip("_")
    return key or "unknown"


def normalize_values(values):
    normalized = {}
    for key, value in values.items():
        normalized[normalize_key(key)] = value
    return normalized


def layout_entry_for_key(layout, key):
    if key in layout:
        return layout[key]
    for candidate_key, spec in layout.items():
        if normalize_key(candidate_key) == key:
            return spec
    return None
```

At the start of `make_payload`, add:

```python
safe_key = normalize_key(key)
```

Use `safe_key` in `unique_id`, `state_topic` value templates, and config topics, while using `layout_entry_for_key()` to find the divider:

```python
"unique_id": f"grott_{device}_{safe_key}",
```

Replace layout lookup:

```python
layout = conf.recorddict[conf.layout]
layout_entry = layout_entry_for_key(layout, safe_key)
if "value_template" not in payload and layout_entry:
    if layout_entry.get("type", "num") in ("num", "numx") and layout_entry.get("divide", "1"):
        payload["value_template"] = "{{{{ value_json.{key} | float / {divide} }}}}".format(
            key=safe_key,
            divide=layout_entry.get("divide"),
        )
```

Replace the default value template:

```python
payload["value_template"] = f"{{{{ value_json.{safe_key} }}}}"
```

In `grottext`, change:

```python
values = normalize_values(jsonmsg["values"])
```

In the config loop, use `safe_key` for the topic:

```python
safe_key = normalize_key(key)
payload = make_payload(conf, device_serial, safe_key, safe_key)
topic = config_topic.format(sensor_type="sensor", device=device_serial, attribut=safe_key)
```

Remove the second explicit last-push config block because `values["grott_last_push"]` is already added before the config loop.

- [ ] **Step 4: Run HA tests**

Run:

```powershell
python -m pytest tests/test_ha_discovery.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit HA discovery fix**

Run:

```powershell
git add "examples/Home Assistent/grott_ha.py" tests/test_ha_discovery.py
git commit -m "fix: sanitize Home Assistant discovery keys"
```

---

### Task 5: Add Docker and HA Add-On Packaging

**Files:**
- Create: `requirements.txt`
- Modify: `docker/dockerfile`
- Modify: `docker/docker-compose.yml`
- Create: `addons/grott/Dockerfile`
- Create: `addons/grott/config.yaml`
- Create: `addons/grott/build.yaml`
- Create: `addons/grott/run.sh`

- [ ] **Step 1: Add runtime dependencies**

Create `requirements.txt`:

```text
paho-mqtt>=1.6,<3
requests>=2.31
influxdb>=5.3
influxdb-client>=1.36
libscrc>=1.8
```

- [ ] **Step 2: Update Dockerfile**

Replace `docker/dockerfile` with:

```dockerfile
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY grott.py grottconf.py grottdata.py grottlayout.py grottproxy.py grottsniffer.py grottserver.py /app/
COPY examples/grott.ini /app/grott.ini
COPY examples/Home\ Assistent/grott_ha.py /app/grott_ha.py
COPY examples/Extensions/grottext.py /app/grottext.py

EXPOSE 5279
CMD ["python", "-u", "grott.py", "-v"]
```

- [ ] **Step 3: Update Docker Compose example**

Replace `docker/docker-compose.yml` with:

```yaml
services:
  grott:
    build:
      context: ..
      dockerfile: docker/dockerfile
    image: grott-ha-docker:local
    container_name: grott
    restart: unless-stopped
    ports:
      - "5279:5279"
    volumes:
      - ./grott.ini:/app/grott.ini:ro
    environment:
      - TZ=Europe/London
      - gmode=proxy
      - gblockcmd=True
      - gtime=server
      - gsendbuf=False
      - ginvtype=default
      - glayoutstrict=False
      - glayoutautofamily=True
      - gnomqtt=True
```

- [ ] **Step 4: Add HA add-on wrapper**

Create `addons/grott/Dockerfile`:

```dockerfile
ARG BUILD_FROM=python:3.11-slim
FROM ${BUILD_FROM}

ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY grott.py grottconf.py grottdata.py grottlayout.py grottproxy.py grottsniffer.py grottserver.py /app/
COPY examples/grott.ini /app/grott.ini
COPY examples/Home\ Assistent/grott_ha.py /app/grott_ha.py
COPY examples/Extensions/grottext.py /app/grottext.py
COPY addons/grott/run.sh /run.sh

RUN chmod a+x /run.sh
EXPOSE 5279
CMD ["/run.sh"]
```

Create `addons/grott/build.yaml`:

```yaml
build_from:
  aarch64: ghcr.io/home-assistant/aarch64-base-python:3.11
  amd64: ghcr.io/home-assistant/amd64-base-python:3.11
  armhf: ghcr.io/home-assistant/armhf-base-python:3.11
  armv7: ghcr.io/home-assistant/armv7-base-python:3.11
  i386: ghcr.io/home-assistant/i386-base-python:3.11
```

Create `addons/grott/config.yaml`:

```yaml
name: Grott HA Docker
version: 0.1.0-beta
slug: grott_ha_docker
description: Growatt proxy telemetry parser with guarded layout selection and Home Assistant MQTT discovery
url: https://github.com/Herbertmt978/grott
arch:
  - aarch64
  - amd64
  - armhf
  - armv7
  - i386
startup: services
boot: auto
init: false
ports:
  5279/tcp: 5279
map:
  - share:rw
services:
  - mqtt:need
options:
  mode: proxy
  blockcmd: true
  time: server
  sendbuf: false
  invtype: default
  layout_strict: false
  layout_auto_family: true
  ha_plugin: true
schema:
  mode: list(proxy|server|sniff)
  blockcmd: bool
  time: list(auto|server)
  sendbuf: bool
  invtype: str
  layout_strict: bool
  layout_auto_family: bool
  ha_plugin: bool
```

Create `addons/grott/run.sh`:

```sh
#!/usr/bin/env sh
set -eu

OPTIONS=/data/options.json

json_get() {
  python - "$OPTIONS" "$1" "$2" <<'PY'
import json
import sys
path, key, default = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
except FileNotFoundError:
    data = {}
print(data.get(key, default))
PY
}

export gmode="$(json_get mode proxy)"
export gblockcmd="$(json_get blockcmd true)"
export gtime="$(json_get time server)"
export gsendbuf="$(json_get sendbuf false)"
export ginvtype="$(json_get invtype default)"
export glayoutstrict="$(json_get layout_strict false)"
export glayoutautofamily="$(json_get layout_auto_family true)"

if [ "$(json_get ha_plugin true)" = "True" ] || [ "$(json_get ha_plugin true)" = "true" ]; then
  export gextension=True
  export gextname=grott_ha
fi

exec python -u /app/grott.py -v
```

- [ ] **Step 5: Build Docker image locally**

Run:

```powershell
docker build -f docker/dockerfile -t grott-ha-docker:test .
```

Expected: image builds successfully.

- [ ] **Step 6: Commit packaging**

Run:

```powershell
git add requirements.txt docker/dockerfile docker/docker-compose.yml addons/grott
git commit -m "feat: add Docker and HA add-on packaging"
```

---

### Task 6: Update Docs and Examples

**Files:**
- Modify: `README.md`
- Modify: `examples/grott.ini`
- Modify: `docs/superpowers/specs/2026-04-25-grott-auto-layout-ha-docker-design.md`

- [ ] **Step 1: Document safe proxy defaults**

Add a section near the top of `README.md`:

```markdown
## Grott HA Docker Fork Beta

This fork keeps upstream Grott history and adds guarded layout selection for Docker and Home Assistant add-on users.

Recommended proxy defaults for Growatt/ShineWiFi telemetry:

```ini
[Generic]
mode = proxy
blockcmd = True
time = server
sendbuf = False
invtype = default
layout_strict = False
layout_auto_family = True
```

Use `layout_strict=True` only when you need legacy forced `invtype` behavior.
```

- [ ] **Step 2: Add example config keys**

In `examples/grott.ini`, add:

```ini
# Guarded layout selection
invtype = default
layout_strict = False
layout_auto_family = True
layout_min_score = 20
```

- [ ] **Step 3: Commit docs**

Run:

```powershell
git add README.md examples/grott.ini docs/superpowers/specs/2026-04-25-grott-auto-layout-ha-docker-design.md
git commit -m "docs: describe guarded layout beta"
```

---

### Task 7: Full Verification

**Files:**
- Test all modified files.

- [ ] **Step 1: Run Python tests**

Run:

```powershell
python -m pytest tests "examples/Home Assistent/test_grott_ha.py" -q
```

Expected: PASS.

- [ ] **Step 2: Run a syntax compile pass**

Run:

```powershell
python -m compileall grott.py grottconf.py grottdata.py grottlayout.py grottproxy.py grottsniffer.py grottserver.py "examples/Home Assistent/grott_ha.py"
```

Expected: all files compile.

- [ ] **Step 3: Build Docker image**

Run:

```powershell
docker build -f docker/dockerfile -t grott-ha-docker:test .
```

Expected: image builds successfully.

- [ ] **Step 4: Check git status**

Run:

```powershell
git status --short
```

Expected: no uncommitted files except intentionally untracked local artifacts.

- [ ] **Step 5: Push branch**

Run:

```powershell
git push origin Herb/auto-layout-ha-docker
```

Expected: branch updates on `Herbertmt978/grott`.

---

## Self-Review

Spec coverage:

- `0103` fallback is covered by Task 1 and Task 3.
- Wrong forced layout rejection is covered by Task 1, Task 2, and Task 3.
- HA illegal topic cleanup is covered by Task 4.
- Docker and HA add-on packaging are covered by Task 5.
- Docs and migration defaults are covered by Task 6.
- Verification and push are covered by Task 7.

Residual risks:

- New firmware `0242` is not fully supported without real fixtures. The first release improves diagnostics and layout extensibility but should not advertise complete `0242` parsing.
- GrottServer command API issues remain second-phase scope.
- HA retained discovery cleanup is conservative in this plan; users moving from bad SPH discovery may still need the documented one-time retained-topic cleanup.
