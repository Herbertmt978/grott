# coding=utf-8
# author Etienne G.

import json
import re
from datetime import datetime, timezone

from paho.mqtt.publish import single, multiple

from grottconf import Conf

__version__ = "0.0.8"

"""A pluging for grott
This plugin allow to have autodiscovery of the device in HA

Should be able to support multiples inverters

Config:
    - ha_mqtt_host (required): The host of the MQTT broker user by HA (often the IP of HA)
    - ha_mqtt_port (required): The port (the default is oftent 1883)
    - ha_mqtt_user (optional): The user use to connect to the broker (you can use your user)
    - ha_mqtt_password (optional): The password to connect to the mqtt broket (you can use your password)

Return codes:
    - 0: Everything is OK
    - 1: Missing MQTT extvar configuration
    - 2: Error while publishing the measure value message
    - 3: MQTT connection error
    - 4: Error while creating last_push status key
    - 5: Refused to push a buffered message (prevent invalid stats, not en error)
    - 6: Error while configuring HA MQTT sensor devices
    - 7: Can't configure device for HA MQTT
"""


config_topic = "homeassistant/{sensor_type}/grott/{device}_{attribut}/config"
state_topic = "homeassistant/grott/{device}/state"

V0_1_9_STANDARD_KEYS = [
    "datalogserial",
    "pvserial",
    "pvstatus",
    "pvpowerin",
    "pv1voltage",
    "pv1current",
    "pv1watt",
    "pv2voltage",
    "pv2current",
    "pv2watt",
    "pvpowerout",
    "pvfrequentie",
    "pvgridvoltage",
    "pvgridcurrent",
    "pvgridpower",
    "pvgridvoltage2",
    "pvgridcurrent2",
    "pvgridpower2",
    "pvgridvoltage3",
    "pvgridcurrent3",
    "pvgridpower3",
    "totworktime",
    "pvenergytoday",
    "pvenergytotal",
    "epvtotal",
    "epv1today",
    "epv1total",
    "epv2today",
    "epv2total",
    "pvtemperature",
    "pvipmtemperature",
    "grott_last_push",
]

SUPPORTED_PROFILE_LAYOUTS = ("T06NNNNX", "T06NNNNXMOD")
MOD_SOURCE_ALIASES = {
    "pac": ("pvpowerout", "pvpowerout"),
    "pvfrequency": ("pvfrequentie", "pvfrequentie"),
    "comboardtemperature": ("pvipmtemperature", "pvipmtemperature"),
    "pvpowerout": ("raw_pvpowerout_r3019", None),
}
COMPATIBILITY_SOURCE_ALIASES = {
    "pvpowerout": ("pac", "pvpowerout", "pvfrequentie"),
    "pvfrequentie": ("pvfrequency", "pvfrequentie", "pvfrequentie"),
    "pvipmtemperature": (
        "comboardtemperature",
        "pvipmtemperature",
        "pvfrequentie",
    ),
}
DISCOVERY_ORIGIN = {"name": "Grott", "sw_version": __version__}


mapping = {
    "datalogserial": {
        "name": "Datalogger serial",
    },
    "pvserial": {"name": "Serial"},
    "pv1watt": {
        "name": "PV1 Watt",
        "state_class": "measurement",
        "device_class": "power",
        "unit_of_measurement": "W",
    },
    "pv1voltage": {
        "name": "PV1 Voltage",
        "state_class": "measurement",
        "device_class": "voltage",
        "unit_of_measurement": "V",
    },
    "pv1current": {
        "name": "PV1 Current",
        "state_class": "measurement",
        "device_class": "current",
        "unit_of_measurement": "A",
    },
    "pv2watt": {
        "name": "PV2 Watt",
        "state_class": "measurement",
        "device_class": "power",
        "unit_of_measurement": "W",
    },
    "pv2voltage": {
        "name": "PV2 Voltage",
        "state_class": "measurement",
        "device_class": "voltage",
        "unit_of_measurement": "V",
    },
    "pv2current": {
        "name": "PV2 Current",
        "state_class": "measurement",
        "device_class": "current",
        "unit_of_measurement": "A",
    },
    "pvpowerin": {
        "name": "PV Input (Actual)",
        "state_class": "measurement",
        "device_class": "power",
        "unit_of_measurement": "W",
    },
    "pvpowerout": {
        "name": "PV Output (Actual)",
        "state_class": "measurement",
        "device_class": "power",
        "unit_of_measurement": "W",
    },
    "pvfrequentie": {
        "name": "Grid frequency",
        "state_class": "measurement",
        "device_class": "frequency",
        "unit_of_measurement": "Hz",
        "icon": "mdi:waveform",
    },
    # Grid config
    "pvgridvoltage": {
        "name": "Phase 1 voltage",
        "state_class": "measurement",
        "device_class": "voltage",
        "unit_of_measurement": "V",
    },
    "pvgridvoltage2": {
        "name": "Phase 2 voltage",
        "state_class": "measurement",
        "device_class": "voltage",
        "unit_of_measurement": "V",
    },
    "pvgridvoltage3": {
        "name": "Phase 3 voltage",
        "state_class": "measurement",
        "device_class": "voltage",
        "unit_of_measurement": "V",
    },
    "pvgridcurrent": {
        "name": "Phase 1 current",
        "state_class": "measurement",
        "device_class": "current",
        "unit_of_measurement": "A",
    },
    "pvgridcurrent2": {
        "state_class": "measurement",
        "device_class": "current",
        "name": "Phase 2 current",
        "unit_of_measurement": "A",
    },
    "pvgridcurrent3": {
        "name": "Phase 3 current",
        "state_class": "measurement",
        "device_class": "current",
        "unit_of_measurement": "A",
    },
    "pvgridpower": {
        "name": "Phase 1 power",
        "state_class": "measurement",
        "device_class": "power",
        "unit_of_measurement": "W",
    },
    "pvgridpower2": {
        "name": "Phase 2 power",
        "state_class": "measurement",
        "device_class": "power",
        "unit_of_measurement": "W",
    },
    "pvgridpower3": {
        "name": "Phase 3 power",
        "state_class": "measurement",
        "device_class": "power",
        "unit_of_measurement": "W",
    },
    # End grid
    "pvenergytoday": {
        "name": "Generated energy (Today)",
        "state_class": "total",
        "device_class": "energy",
        "unit_of_measurement": "kWh",
        "icon": "mdi:solar-power",
    },
    "epvtoday": {
        "name": "PV Energy today (Today)",
        "state_class": "total",
        "device_class": "energy",
        "unit_of_measurement": "kWh",
        "icon": "mdi:solar-power",
    },
    "epv1today": {
        "name": "Solar PV1 production",
        "state_class": "total",
        "device_class": "energy",
        "unit_of_measurement": "kWh",
        "icon": "mdi:solar-power",
    },
    "epv2today": {
        "name": "Solar PV2 production",
        "state_class": "total",
        "device_class": "energy",
        "unit_of_measurement": "kWh",
        "icon": "mdi:solar-power",
    },
    "pvenergytotal": {
        "state_class": "total_increasing",
        "device_class": "energy",
        "name": "Generated energy (Total)",
        "unit_of_measurement": "kWh",
        "icon": "mdi:solar-power",
    },
    "epvtotal": {
        "name": "Generated PV energy (Total)",
        "device_class": "energy",
        "unit_of_measurement": "kWh",
        "icon": "mdi:solar-power",
        "state_class": "total",
    },
    "epv1total": {
        "name": "Solar PV1 production (Total)",
        "state_class": "total",
        "device_class": "energy",
        "unit_of_measurement": "kWh",
        "icon": "mdi:solar-power",
    },
    "epv2total": {
        "name": "Solar PV2 production (Total)",
        "state_class": "total",
        "device_class": "energy",
        "unit_of_measurement": "kWh",
        "icon": "mdi:solar-power",
    },
    # For SPH compatiblity
    "epvTotal": {
        "name": "Generated PV energy (Total)",
        "device_class": "energy",
        "unit_of_measurement": "kWh",
        "icon": "mdi:solar-power",
        "state_class": "total",
    },
    "pactogridr": {
        "name": "Energy export (Today)",
        "device_class": "energy",
        "state_class": "measurement",
        "unit_of_measurement": "Wh",
        "state_class": "total",
        "icon": "mdi:solar-power",
    },
    "pactogridtot": {
        "name": "Energy export (Total)",
        "device_class": "energy",
        "state_class": "measurement",
        "unit_of_measurement": "Wh",
        "state_class": "total_increasing",
        "icon": "mdi:solar-power",
    },
    "pvstatus": {
        "name": "State",
        # "value_template": "{% if value_json.pvstatus == 0 %}Standby{% elif value_json.pvstatus == 1 %}Normal{% elif value_json.pvstatus == 2 %}Fault{% else %}Unknown{% endif %}",
        "icon": "mdi:power-settings",
    },
    "totworktime": {
        "name": "Working time",
        "device_class": "duration",
        "unit_of_measurement": "h",
        "value_template": "{{ value_json.totworktime| float / 7200 | round(2) }}",
    },
    "pvtemperature": {
        "name": "Inverter temperature",
        "state_class": "measurement",
        "device_class": "temperature",
        "unit_of_measurement": "°C",
    },
    "pvipmtemperature": {
        "name": "IPM temperature",
        "device_class": "temperature",
        "unit_of_measurement": "°C",
        "state_class": "measurement",
    },
    "pvboottemperature": {
        "name": "Inverter boost temperature",
        "device_class": "temperature",
        "unit_of_measurement": "°C",
        "state_class": "measurement",
    },
    "pvboosttemp": {
        "name": "Inverter boost temperature",
        "device_class": "temperature",
        "unit_of_measurement": "°C",
        "state_class": "measurement",
    },
    "etogrid_tod": {
        "name": "Energy to grid (Today)",
        "device_class": "energy",
        "unit_of_measurement": "kWh",
        "icon": "mdi:transmission-tower-import",
        "state_class": "total",
    },
    "etogrid_tot": {
        "name": "Energy to grid (Total)",
        "device_class": "energy",
        "unit_of_measurement": "kWh",
        "icon": "mdi:transmission-tower-import",
        "state_class": "total_increasing",
    },
    "etouser_tod": {
        "name": "Import from grid (Today)",
        "device_class": "energy",
        "unit_of_measurement": "kWh",
        "icon": "mdi:solar-power",
        "state_class": "total",
    },
    "etouser_tot": {
        "name": "Import from grid (Total)",
        "device_class": "energy",
        "unit_of_measurement": "kWh",
        "icon": "mdi:transmission-tower-export",
        "state_class": "total_increasing",
    },
    "pactouserr": {
        "name": "Import from grid (Actual)",
        "device_class": "energy",
        "device_class": "power",
        "unit_of_measurement": "W",
        "icon": "mdi:transmission-tower-export",
    },
    # Register 1015 # TODO: investiagate
    # "pactousertot": {
    #     "name": "Power consumption total",
    #     "device_class": "power",
    #     "unit_of_measurement": "kW",
    #     "icon": "mdi:transmission-tower-export",
    # },
    "elocalload_tod": {
        "name": "Load consumption (Today)",
        "device_class": "energy",
        "unit_of_measurement": "Wh",
        "icon": "mdi:solar-power",
        "state_class": "total",
    },
    "elocalload_tot": {
        "name": "Load consumption (Total)",
        "device_class": "energy",
        "unit_of_measurement": "Wh",
        "icon": "mdi:solar-power",
        "state_class": "total_increasing",
    },
    "plocaloadr": {
        "name": "Local load consumption",
        "device_class": "power",
        "unit_of_measurement": "W",
        "icon": "mdi:transmission-tower-export",
    },
    "grott_last_push": {
        "name": "Grott last data push",
        "device_class": "timestamp",
        "value_template": "{{value_json.grott_last_push}}",
        "expire_after": 900,
    },
    "grott_last_measure": {
        "name": "Last measure",
        "device_class": "timestamp",
    },
    # batteries
    "eacharge_today": {
        "name": "Battery charge from AC (Today)",
        "device_class": "energy",
        "unit_of_measurement": "kWh",
        "icon": "mdi:battery-arrow-up",
        "state_class": "total",
    },
    "eacharge_total": {
        "name": "Battery charge from AC (Today)",
        "device_class": "energy",
        "unit_of_measurement": "kWh",
        "icon": "mdi:solar-power",
        "state_class": "total_increasing",
    },
    "vbat": {
        "name": "Battery voltage",
        "state_class": "measurement",
        "device_class": "voltage",
        "unit_of_measurement": "V",
    },
    "SOC": {
        "name": "Battery charge",
        "device_class": "battery",
        "state_class": "measurement",
        "unit_of_measurement": "%",
        "icon": "mdi:battery-charging-60",
    },
    # taken from register 1048 of RTU manual v1.20
    "batterytype": {
        "name": "Batteries type",
        "value_template": "{% if value_json.batterytype == 0 %}Lithium{% elif value_json.batterytype == '1' %}Lead-acid{% elif value_json.batterytype == '2' %}Other{% else %}Unknown{% endif %}",
        "icon": "mdi:power-settings",
    },
    "p1charge1": {
        "name": "Battery charge",
        "device_class": "power",
        "unit_of_measurement": "kW",
        "state_class": "measurement",
        "icon": "mdi:battery-arrow-up",
    },
    "eharge1_tod": {
        "name": "Battery charge (Today)",
        "device_class": "energy",
        "state_class": "total",
        "unit_of_measurement": "kWh",
        "icon": "mdi:battery-arrow-up",
    },
    "eharge1_tot": {
        "name": "Battery charge (Total)",
        "device_class": "energy",
        "state_class": "total_increasing",
        "unit_of_measurement": "kWh",
        "icon": "mdi:battery-arrow-up",
    },
    "edischarge1_tod": {
        "name": "Battery discharge (Today)",
        "device_class": "energy",
        "state_class": "total",
        "unit_of_measurement": "kWh",
        "icon": "mdi:battery-arrow-down",
    },
    "edischarge1_tot": {
        "name": "Battery discharge (Total)",
        "device_class": "energy",
        "state_class": "total_increasing",
        "unit_of_measurement": "kWh",
        "icon": "mdi:battery-arrow-down",
    },
    "battemp": {
        "name": "Battery temperature",
        "device_class": "temperature",
        "unit_of_measurement": "°C",
        "icon": "mdi:thermometer",
    },
    "spbusvolt": {
        "state_class": "measurement",
        "device_class": "voltage",
        "name": "BP bus voltage",
        "unit_of_measurement": "V",
    },
    "systemfaultword1": {
        "name": "System fault register 1",
    },
    "systemfaultword2": {
        "name": "System fault register 2",
    },
    "systemfaultword3": {
        "name": "System fault register 3",
    },
    "systemfaultword4": {
        "name": "System fault register 4",
    },
    "systemfaultword5": {
        "name": "System fault register 5",
    },
    "systemfaultword6": {
        "name": "System fault register 6",
    },
    "systemfaultword7": {
        "name": "System fault register 7",
    },
    "vpv1": {
        "name": "PV1 Voltage",
        "state_class": "measurement",
        "device_class": "voltage",
        "unit_of_measurement": "V",
    },
    "vpv2": {
        "name": "PV2 Voltage",
        "state_class": "measurement",
        "device_class": "voltage",
        "unit_of_measurement": "V",
    },
    "ppv1": {
        "name": "PV1 charge power",
        "device_class": "power",
        "unit_of_measurement": "W",
        "state_class": "measurement",
    },
    "ppv2": {
        "name": "PV1 charge power",
        "device_class": "power",
        "unit_of_measurement": "W",
        "state_class": "measurement",
    },
    "buck1curr": {
        "name": "Buck1 current",
        "device_class": "current",
        "unit_of_measurement": "A",
        "state_class": "measurement",
    },
    "buck2curr": {
        "name": "Buck2 current",
        "device_class": "current",
        "unit_of_measurement": "A",
        "state_class": "measurement",
    },
    "op_watt": {
        "name": "Output active power",
        "device_class": "power",
        "unit_of_measurement": "W",
        "state_class": "measurement",
    },
    "op_va": {
        "name": "Output apparent power",
        "device_class": "apparent_power",
        "unit_of_measurement": "VA",
        "state_class": "measurement",
    },
}


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


def record_layout(conf: Conf, layout_name: str):
    for candidate_name, layout in conf.recorddict.items():
        if str(candidate_name).upper() == layout_name.upper():
            return layout
    return None


def layout_entry_for_conf(conf: Conf, key: str, canonical: bool = False):
    if canonical and profile_applies(conf):
        layout_names = ("T06NNNNXMOD", "T06NNNNX")
    else:
        layout_names = (str(conf.layout),)

    for layout_name in layout_names:
        layout = record_layout(conf, layout_name)
        if layout:
            entry = layout_entry_for_key(layout, key)
            if entry:
                return entry
    return None


def make_payload(
    conf: Conf,
    device: str,
    name: str,
    key: str,
    unit: str = None,
    source_key: str = None,
    fallback_key: str = None,
    fallback_guard_key: str = None,
    diagnostic: bool = False,
    canonical: bool = False,
):
    safe_key = normalize_key(key)
    safe_source_key = normalize_key(source_key or key)
    # Default configuration payload
    payload = {
        "name": "{name}",
        "unique_id": f"grott_{device}_{safe_key}",  # Generate a unique device ID
        "state_topic": f"homeassistant/grott/{device}/state",
        "origin": DISCOVERY_ORIGIN,
        "device": {
            "identifiers": [device],  # Group under a device
            "name": device,
            "manufacturer": "GroWatt",
        },
    }

    # If there's a custom mapping add the new values
    if safe_key in mapping:
        payload.update(mapping[safe_key])

    if diagnostic:
        payload["entity_category"] = "diagnostic"

    # Reuse the existing divide value if available and not existing
    # and apply it to the HA config
    layout_entry = layout_entry_for_conf(conf, safe_source_key, canonical=canonical)
    if not layout_entry and fallback_key:
        layout_entry = layout_entry_for_conf(
            conf, normalize_key(fallback_key), canonical=canonical
        )
    if "value_template" not in payload and layout_entry:
        # From grottdata:207, default type is num, also process numx
        if layout_entry.get("type", "num") in ("num", "numx") and layout_entry.get("divide", "1"):
            divide = layout_entry.get("divide", "1")
            if fallback_key:
                safe_fallback_key = normalize_key(fallback_key)
                safe_fallback_guard_key = normalize_key(
                    fallback_guard_key or fallback_key
                )
                payload["value_template"] = (
                    "{% if value_json."
                    + safe_source_key
                    + " is defined %}{{ value_json."
                    + safe_source_key
                    + " | float / "
                    + str(divide)
                    + " }}{% elif value_json."
                    + safe_fallback_guard_key
                    + " is defined %}{{ value_json."
                    + safe_fallback_key
                    + " | float / "
                    + str(divide)
                    + " }}{% endif %}"
                )
            else:
                payload["value_template"] = "{{{{ value_json.{key} | float / {divide} }}}}".format(
                    key=safe_source_key,
                    divide=divide,
                )

    if "value_template" not in payload:
        payload["value_template"] = f"{{{{ value_json.{safe_source_key} }}}}"

    payload["name"] = f"{device} {payload['name'].format(name=name)}"

    return payload


class MqttStateHandler:
    __pv_config = {}
    client_name = "Grott - HA"

    @classmethod
    def is_configured(cls, serial: str):
        return cls.__pv_config.get(serial, False)

    @classmethod
    def configuration_for(cls, serial: str):
        return cls.__pv_config.get(serial)

    @classmethod
    def set_configured(cls, serial: str, signature, desired_topics):
        previous = cls.configuration_for(serial)
        carried_messages = []
        if isinstance(previous, dict):
            seen_topics = set()
            for batch in previous.get("cleanup_batches", []):
                for message in batch["messages"]:
                    topic = message["topic"]
                    if topic not in desired_topics and topic not in seen_topics:
                        carried_messages.append(message)
                        seen_topics.add(topic)

        cleanup_batches = []
        cleanup_signatures = set()
        if carried_messages:
            carried_signature = ("carried", signature)
            cleanup_batches.append(
                {"signature": carried_signature, "messages": carried_messages}
            )
            cleanup_signatures.add(carried_signature)

        cls.__pv_config[serial] = {
            "signature": signature,
            "desired_topics": set(desired_topics),
            "cleanup_batches": cleanup_batches,
            "cleanup_signatures": cleanup_signatures,
            "reconciled_cleanup": set(),
        }

    @classmethod
    def desired_topics(cls, serial: str):
        config = cls.configuration_for(serial)
        return config.get("desired_topics", set()) if isinstance(config, dict) else set()

    @classmethod
    def queue_cleanup(cls, serial: str, signature, messages):
        config = cls.configuration_for(serial)
        if not isinstance(config, dict):
            return
        if signature in config["reconciled_cleanup"]:
            return
        if signature in config["cleanup_signatures"]:
            return

        desired_topics = config["desired_topics"]
        pending_topics = {
            message["topic"]
            for batch in config["cleanup_batches"]
            for message in batch["messages"]
        }
        filtered = [
            message
            for message in messages
            if message["topic"] not in desired_topics
            and message["topic"] not in pending_topics
        ]
        if not filtered:
            config["reconciled_cleanup"].add(signature)
            return

        config["cleanup_batches"].append(
            {"signature": signature, "messages": filtered}
        )
        config["cleanup_signatures"].add(signature)

    @classmethod
    def cleanup_topics(cls, serial: str):
        config = cls.configuration_for(serial)
        if not isinstance(config, dict) or not config["cleanup_batches"]:
            return []
        return config["cleanup_batches"][0]["messages"]

    @classmethod
    def complete_cleanup(cls, serial: str):
        config = cls.configuration_for(serial)
        if not isinstance(config, dict) or not config["cleanup_batches"]:
            return
        batch = config["cleanup_batches"].pop(0)
        signature = batch["signature"]
        config["cleanup_signatures"].discard(signature)
        config["reconciled_cleanup"].add(signature)


def process_conf(conf: Conf):
    required_params = [
        "ha_mqtt_host",
        "ha_mqtt_port",
    ]
    if not all([param in conf.extvar for param in required_params]):
        print("Missing configuration for ha_mqtt")
        raise AttributeError

    if "ha_mqtt_user" in conf.extvar:
        auth = {
            "username": conf.extvar["ha_mqtt_user"],
            "password": conf.extvar["ha_mqtt_password"],
        }
    else:
        auth = None

    # Need to convert the port if passed as a string
    port = conf.extvar["ha_mqtt_port"]
    if isinstance(port, str):
        port = int(port)
    return {
        "client_id": MqttStateHandler.client_name,
        "auth": auth,
        "hostname": conf.extvar["ha_mqtt_host"],
        "port": port,
    }


def publish_single(conf: Conf, topic, payload, retain=False):
    conf = process_conf(conf)
    return single(topic, payload=payload, retain=retain, **conf)


def publish_multiple(conf: Conf, msgs):
    conf = process_conf(conf)
    return multiple(msgs, **conf)


def entity_profile(conf: Conf):
    profile = conf.extvar.get("ha_entity_profile", "v0_1_9_standard")
    if profile not in ("v0_1_9_standard", "all"):
        raise ValueError(
            "Invalid ha_entity_profile {!r}; expected v0_1_9_standard or all".format(
                profile
            )
        )
    return profile


def profile_applies(conf: Conf):
    return str(getattr(conf, "layout", "")).upper() in SUPPORTED_PROFILE_LAYOUTS


def layout_definition_keys(layout, include_excluded=False):
    return [
        normalize_key(key)
        for key, entry in layout.items()
        if isinstance(entry, dict)
        and "length" in entry
        and (include_excluded or entry.get("incl") != "no")
    ]


def layout_value_keys(conf: Conf):
    layout = record_layout(conf, str(conf.layout))
    return (
        layout_definition_keys(
            layout,
            include_excluded=bool(getattr(conf, "includeall", False)),
        )
        if layout
        else []
    )


def supported_profile_value_keys(conf: Conf):
    keys = []
    seen = set()
    for layout_name in SUPPORTED_PROFILE_LAYOUTS:
        layout = record_layout(conf, layout_name)
        if not layout:
            continue
        # Cleanup must also know about topics created by an earlier includeall=True run.
        for key in layout_definition_keys(layout, include_excluded=True):
            if key not in seen:
                keys.append(key)
                seen.add(key)
    return keys


def standard_component(key):
    source, fallback, fallback_guard = COMPATIBILITY_SOURCE_ALIASES.get(
        key, (key, None, None)
    )
    return (key, source, fallback, fallback_guard, False, True)


def discovery_signature(conf: Conf, profile):
    layout = str(getattr(conf, "layout", "")).upper()
    if profile == "v0_1_9_standard" and profile_applies(conf):
        return (profile, "T06NNNNX_FAMILY")
    if profile == "all" and profile_applies(conf):
        return (profile, layout, bool(getattr(conf, "includeall", False)))
    return (profile, layout)


def discovery_components(conf: Conf, values, profile):
    """Return stable discovery identities, sources and presentation metadata."""
    if not profile_applies(conf):
        return [(key, key, None, None, False, False) for key in values]

    components = [standard_component(key) for key in V0_1_9_STANDARD_KEYS]
    if profile == "v0_1_9_standard":
        return components

    aliases = (
        MOD_SOURCE_ALIASES
        if str(conf.layout).upper() == "T06NNNNXMOD"
        else {}
    )
    seen_identities = {component[0] for component in components}
    for source in layout_value_keys(conf):
        identity, _ = aliases.get(source, (source, None))
        if identity in seen_identities:
            continue
        components.append(
            (identity, source, None, None, True, False)
        )
        seen_identities.add(identity)
    return components


def config_message(
    conf: Conf,
    device_serial,
    identity,
    source,
    fallback,
    fallback_guard,
    diagnostic,
    canonical,
):
    payload = make_payload(
        conf,
        device_serial,
        identity,
        identity,
        source_key=source,
        fallback_key=fallback,
        fallback_guard_key=fallback_guard,
        diagnostic=diagnostic,
        canonical=canonical,
    )
    return {
        "topic": config_topic.format(
            sensor_type="sensor", device=device_serial, attribut=normalize_key(identity)
        ),
        "payload": json.dumps(payload),
        "qos": 1,
        "retain": True,
    }


def cleanup_messages(conf: Conf, device_serial, desired_topics):
    candidate_keys = []
    seen = set()

    def add_candidate(key):
        safe_key = normalize_key(key)
        if safe_key not in seen:
            candidate_keys.append(safe_key)
            seen.add(safe_key)

    for key in V0_1_9_STANDARD_KEYS:
        add_candidate(key)
    for key in supported_profile_value_keys(conf):
        add_candidate(key)
    for source, (identity, _) in MOD_SOURCE_ALIASES.items():
        add_candidate(source)
        add_candidate(identity)

    return [
        {
            "topic": config_topic.format(
                sensor_type="sensor", device=device_serial, attribut=key
            ),
            "payload": "",
            "qos": 1,
            "retain": True,
        }
        for key in candidate_keys
        if config_topic.format(
            sensor_type="sensor", device=device_serial, attribut=key
        ) not in desired_topics
    ]


def grottext(conf: Conf, data: str, jsonmsg: str):
    """Allow to push to HA MQTT bus, with auto discovery"""

    required_params = [
        "ha_mqtt_host",
        "ha_mqtt_port",
    ]
    if not all([param in conf.extvar for param in required_params]):
        print("Missing configuration for ha_mqtt")
        return 1

    try:
        profile = entity_profile(conf)
    except ValueError as exc:
        print(f"\t[Grott HA] {__version__} {exc}")
        return 6

    # Need to decode the json string
    jsonmsg = json.loads(jsonmsg)

    if jsonmsg.get("buffered") == "yes":
        # Skip buffered message, HA don't support them
        if conf.verbose:
            print("\t - Grott HA - skipped buffered")
        return 5

    device_serial = jsonmsg["device"]
    # Keep the complete, normalised state contract used by earlier releases.
    # The entity profile filters discovery only; it never filters state values.
    values = normalize_values(jsonmsg["values"])

    # Send the last push in UTC with TZ
    dt = datetime.now(timezone.utc)
    # Add a new value to the existing values
    values["grott_last_push"] = dt.isoformat()
    discovery_values = values

    signature = discovery_signature(conf, profile)
    configured = MqttStateHandler.configuration_for(device_serial)

    # Layout can be undefined
    if (
        getattr(conf, "layout", None)
        and (not isinstance(configured, dict) or configured.get("signature") != signature)
    ):
        configs_payloads = []
        print(f"\tGrott HA {__version__} - creating {device_serial} config in HA")
        for (
            identity,
            source,
            fallback,
            fallback_guard,
            diagnostic,
            canonical,
        ) in discovery_components(conf, discovery_values, profile):
            # Generate a configuration payload
            try:
                configs_payloads.append(
                    config_message(
                        conf,
                        device_serial,
                        identity,
                        source,
                        fallback,
                        fallback_guard,
                        diagnostic,
                        canonical,
                    )
                )
            except Exception as e:
                print(
                    f"\t - [grott HA] {__version__} Exception while creating new sensor {identity}: {e}"
                )
                return 6

        try:
            publish_multiple(conf, configs_payloads)
        except Exception as e:
            print(f"\t[Grott HA] {__version__} Exception while publishing discovery: {e}")
            return 6
        if conf.verbose:
            print(
                f"\tGrott HA {__version__} - published "
                f"{len(configs_payloads)} Home Assistant discovery topics for {device_serial}"
            )
        # Desired configurations are retained before stale same-device topics are removed.
        desired_topics = {message["topic"] for message in configs_payloads}
        MqttStateHandler.set_configured(
            device_serial,
            signature,
            desired_topics,
        )

    if not MqttStateHandler.is_configured(device_serial):
        print(f"\t[Grott HA] {__version__} Can't configure device: {device_serial}")
        return 7

    if profile_applies(conf):
        desired_topics = MqttStateHandler.desired_topics(device_serial)
        MqttStateHandler.queue_cleanup(
            device_serial,
            signature,
            cleanup_messages(conf, device_serial, desired_topics),
        )

    pending_cleanup = MqttStateHandler.cleanup_topics(device_serial)
    if pending_cleanup:
        try:
            publish_multiple(conf, pending_cleanup)
            MqttStateHandler.complete_cleanup(device_serial)
        except Exception as e:
            print(f"\t[Grott HA] {__version__} Exception while cleaning discovery: {e}")

    # Push the vales to the topics
    try:
        publish_single(
            conf, state_topic.format(device=device_serial), json.dumps(values)
        )
        if conf.verbose:
            print(
                f"\tGrott HA {__version__} - published Home Assistant state topic "
                f"for {device_serial}"
            )
    except Exception as e:
        print("[HA ext] - Exception while publishing - {}".format(e))
        # Reset connection state in case of problem
        return 2
    return 0
