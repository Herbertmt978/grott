# Grott HA Docker

Grott HA Docker is a beta Home Assistant add-on for Growatt/ShineWiFi telemetry. It runs Grott in proxy mode, forwards packets to Growatt, and publishes sane Home Assistant MQTT discovery using guarded layout selection.

## Recommended Setup

Point each Growatt/ShineWiFi datalogger at your Home Assistant host on port `5279`.

Default options are intentionally conservative:

```yaml
mode: proxy
blockcmd: true
time: server
sendbuf: false
invtype: default
layout_strict: false
layout_auto_family: true
ha_plugin: true
```

Use `layout_strict: true` only if you need legacy forced `invtype` behavior.

## MQTT

The add-on needs an MQTT broker reachable from the container. For the Mosquitto add-on, the default host is usually `core-mosquitto` and the default port is `1883`.

If your broker requires authentication, set `mqtt_user` and `mqtt_password` in the add-on options.

## Migration From Existing Grott Add-Ons

If a previous Grott install published the wrong Home Assistant discovery entities, you may need to clear the retained discovery topics for the affected Grott devices once. Do this only for Grott-owned topics under:

```text
homeassistant/sensor/grott/
```

Stable corrected sensors should then be recreated automatically on the next live packet.
