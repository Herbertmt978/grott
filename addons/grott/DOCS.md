# Grott HA Docker

Grott HA Docker is a beta Home Assistant add-on for Growatt/ShineWiFi telemetry. It runs Grott in proxy mode, forwards packets to Growatt, and publishes sane Home Assistant MQTT discovery using guarded layout selection.

This add-on uses the prebuilt GHCR image `ghcr.io/herbertmt978/grott-ha-docker`. Home Assistant should download the image tag matching the add-on version instead of building on the HA box.

This release is beta software. It has been tested with a real ShineWiFi/SPH Home Assistant setup and the packet fixtures in the repository, but other Growatt inverter families may still need new sanitized fixtures before every sensor is correct.

`0.1.1-beta` was skipped for users because its first multi-architecture image publish failed. Install `0.1.5-beta` or newer.

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
mqtt_host: core-mosquitto
mqtt_port: 1883
mqtt_retain: false
```

Use `layout_strict: true` only if you need legacy forced `invtype` behavior.

## First Run

1. Start the add-on.
2. Check the log for the selected options and MQTT connection.
3. Wait for the datalogger to send a fresh packet.
4. Confirm the log shows a parsed Growatt record and an MQTT publish.
5. Open the MQTT integration in Home Assistant and check for the Grott device and sensors.

If the add-on starts but no packets arrive, check the datalogger server setting, the Home Assistant IP address, and any firewall rule between the datalogger and port `5279`.

## MQTT

The add-on needs an MQTT broker reachable from the container. For the Mosquitto add-on, the default host is usually `core-mosquitto` and the default port is `1883`.

If your broker requires authentication, set `mqtt_user` and `mqtt_password` in the add-on options.

The add-on publishes Home Assistant MQTT discovery through the maintained `grott-ha-plugin` extension (`grottext.ha`). Discovery config topics are retained so sensors survive Home Assistant restarts. Live state messages are not retained by default; enable `mqtt_retain` only if you understand the stale-state trade-off.

## Migration From Existing Grott Add-Ons

If a previous Grott install published the wrong Home Assistant discovery entities, you may need to clear the retained discovery topics for the affected Grott devices once. Do this only for Grott-owned topics under:

```text
homeassistant/sensor/grott/
```

Stable corrected sensors should then be recreated automatically on the next live packet.

Dry-run the cleanup helper first from a machine that can reach your MQTT broker:

```sh
python tools/ha_discovery_cleanup.py --host MQTT_HOST --device DATALOGGER_SERIAL --all
```

If the listed topics are only the stale Grott discovery topics you intend to remove, run it again with `--execute`:

```sh
python tools/ha_discovery_cleanup.py --host MQTT_HOST --device DATALOGGER_SERIAL --all --execute
```

Use `--keep pvpowerout,SOC` instead of `--all` when you want to preserve known-good attributes and only clear legacy leftovers.

## Rollback

If this beta does not behave on your system, stop the add-on and reinstall the Grott add-on repository you used before. If you were running Grott in Docker, change your compose file back to the previous image tag, for example:

```yaml
image: ledidobe/grott:2.8.3_240731
```

Keep the dataloggers pointed at the same host and port only if the replacement Grott service is listening on TCP `5279`.
