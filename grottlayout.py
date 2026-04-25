import codecs
import re
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Tuple


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
            logdict = (
                bytes.fromhex(result_string[start : len(result_string) - 4])
                .decode("ASCII")
                .split(",")
            )
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
                    values[key] = int.from_bytes(
                        bytes.fromhex(raw_value), byteorder="big", signed=True
                    )
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


def divided(
    layout_def: Mapping[str, Mapping[str, object]],
    values: Mapping[str, object],
    key: str,
) -> Optional[float]:
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

    for key in (
        "pvgridvoltage",
        "pvgridvoltage2",
        "pvgridvoltage3",
        "pv1voltage",
        "pv2voltage",
    ):
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

    battery_keys = [
        key
        for key in ("SOC", "vbat", "batterytype", "bmsbatteryvolt", "bmsbatterycurr")
        if key in layout_def
    ]
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


def _append_unique(
    candidates: List[LayoutCandidate],
    layout: str,
    reason: str,
    preferred: bool = False,
) -> None:
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


def select_layout(
    conf,
    header: str,
    ndata: int,
    is_smart_meter: bool,
    result_string: str,
    current_layout: str,
) -> LayoutSelection:
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
        values, errors = parse_layout_values(
            layout_def, result_string, getattr(conf, "includeall", False)
        )
        score, reasons = score_layout(
            candidate.layout, layout_def, values, errors, candidate.preferred
        )
        if reasons:
            rejected[candidate.layout] = reasons
        selection = LayoutSelection(candidate.layout, score, values, errors, rejected.copy())
        if best is None or selection.score > best.score:
            best = selection

    min_score = int(getattr(conf, "layout_min_score", 20))
    if best and (best.score >= min_score or best.layout == current_layout):
        return best
    return LayoutSelection(current_layout, best.score if best else 0, rejected=rejected)
