# Grott HA Docker

Grott HA Docker is a beta Home Assistant add-on for Growatt/ShineWiFi telemetry. It runs Grott in proxy mode, forwards packets to Growatt, and publishes sane Home Assistant MQTT discovery using guarded layout selection.

The latest supported release is `0.1.9-beta`; `0.1.10-beta` and `0.1.11-beta` remain published prereleases. Supported release candidate: `0.1.12-beta`, currently an unpublished local UAT candidate for the external-layout packaging correction. Its [human-written release notes](../../docs/releases/v0.1.12-beta.md) explain the problem, fix, reason, and user benefit. Only after the 24-hour VM soak and every publication gate pass will this add-on use `ghcr.io/herbertmt978/grott-ha-docker:0.1.12-beta`; Home Assistant selects the image tag matching the add-on version instead of building on the HA box.

This release is beta software. It is being tested with two real ShineWiFi/MOD inverters. SPH, MIN, TL3, T060120, and other unavailable families are fixture/container tested, not real-hardware tested, and may still need new sanitized fixtures before every sensor is correct.

| Version domain | Version | Meaning |
| --- | --- | --- |
| Fork/add-on release | `0.1.12-beta` | The unpublished candidate add-on metadata and matching local test image. |
| Bundled Grott core (upstream startup version) | `2.8.3` | The version printed at startup by the inherited Grott entry point, with this fork's fixes applied on top. |
| Bundled Home Assistant extension | `0.0.8` | The in-repository MQTT discovery and state extension. |

`0.1.1-beta` was skipped for users because its first multi-architecture image publish failed. The exact previous live rollback version for this candidate is `0.1.11-beta`; `0.1.9-beta` remains Latest.

The public tag may not exist until every release gate passes. On 2026-07-14, the fork owner confirmed that upstream redistribution permission has been obtained for this fork and its container images. That permission does not authorize commercial use or reuse unless Johan Meijer has separately agreed, and any financial reward or appreciation is directed to him. See the root `RELEASING.md`, `docs/LEGAL.md`, and [johanmeijer/grott#512](https://github.com/johanmeijer/grott/issues/512).

## Recommended Setup

Point each Growatt/ShineWiFi datalogger at your Home Assistant host on port `5279`.

This add-on is configured around the official [Mosquitto broker add-on](https://github.com/home-assistant/addons/blob/master/mosquitto/DOCS.md) and the official [MQTT integration](https://www.home-assistant.io/integrations/mqtt/) with MQTT discovery enabled.

Default options are intentionally conservative:

```yaml
mode: proxy
blockcmd: true
time: server
sendbuf: false
invtype: default
layout_strict: false
layout_auto_family: true
diagnostic_logging: false
ha_plugin: true
ha_entity_profile: v0_1_9_standard
mqtt_host: core-mosquitto
mqtt_port: 1883
mqtt_user: ""
mqtt_password: ""
mqtt_retain: false
```

Use `layout_strict: true` only if you need legacy forced `invtype` behavior.

This add-on exposes proxy mode only. With `ha_plugin: true`, the bundled Home Assistant extension publishes MQTT discovery and state while Grott's separate native MQTT publisher is disabled, preventing two MQTT publishing paths from running together.

## Home Assistant Entity Profile

`ha_entity_profile` controls Home Assistant MQTT discovery only and accepts exactly two values:

- `v0_1_9_standard` is the default for the `T06NNNNXMOD` and `T06NNNNX` decoded layouts. Its 31 layout-derived entities plus `grott_last_push` reproduce the 32 Home Assistant entities discovered by the verified standard-Docker `v0.1.9-beta` setup.
- `all` exposes the complete active `T06NNNNXMOD` map: 170 layout-derived entities plus `grott_last_push`, for 171 discovered entities. Generic `T06NNNNX` remains at the verified 32 entities with the normal `includeall=False` parser setting. If Grott's separate `includeall=True` setting is enabled, `all` also discovers fields marked `incl: no`: 205 entities for MOD and 36 for generic. Other decoded layouts retain their existing discovery behavior under either profile.

Neither profile changes Grott packet parsing, validation, or forwarding. The full Home Assistant state JSON is unchanged: it still contains every normalized decoded packet value plus `grott_last_push`; the profile filters discovery only. Grott's native raw telemetry is also unchanged. With `ha_plugin: true`, `gnomqtt=True` still disables the separate native MQTT publisher and the Home Assistant extension remains the discovery and state publisher.

The exact default add-on setting is:

```yaml
ha_entity_profile: v0_1_9_standard
```

To opt into the complete distinct layout map, use:

```yaml
ha_entity_profile: all
```

With the default unauthenticated broker options, the add-on emits this exact `gextvar` JSON:

```json
{"ha_mqtt_host":"core-mosquitto","ha_mqtt_port":1883,"ha_mqtt_retain":false,"ha_entity_profile":"v0_1_9_standard"}
```

Selecting `all` changes only the final value to `"ha_entity_profile":"all"`; authenticated configurations also include the configured `ha_mqtt_user` and `ha_mqtt_password`. These examples contain no real or stable device identifiers.

The default profile publishes the desired retained configs at QoS 1 first, then sends retained empty QoS 1 tombstones only for known Grott config topics in the same device's supported generic/MOD lineage that fall outside the verified 32-entity set. This removes extras left by an earlier MOD `all` or `includeall=True` run even when the first post-upgrade packet is generic. It does not scan the broker or remove another device's or integration's discovery configs. If cleanup fails, Grott logs it, still publishes the full state, and retries the exact cleanup on the next packet. A missing option defaults to `v0_1_9_standard`, so an upgrade triggers cleanup on its next live packet.

Create and verify **Grott pre-update rollback** before upgrading or changing profiles. The backup must cover the Home Assistant entity registry/configuration and Mosquitto retained MQTT data, or be paired with a separate broker snapshot. Image rollback alone does not restore discovery configs, customizations, statistics, or repairs changed by the migration. After rollback, wait for a fresh packet and verify the expected entities, `grott_last_push`, and Home Assistant Repairs.

## First Run

1. Create a full Home Assistant **Backup** named **Grott pre-update rollback**, wait for it to complete, verify it is visible and contains this add-on/configuration, and record its backup ID plus the current add-on options. Without that verified backup, UAT must not begin.
2. Confirm only the intended add-on or forwarder owns TCP port `5279`; do not start two listeners.
3. Start the add-on and check the log for the selected options and MQTT connection.
4. Wait for the datalogger to send a fresh packet.
5. Confirm the log shows whether a packet was blocked, forwarded-only, or fully parsed and published.
6. Open the MQTT integration in Home Assistant and check for the Grott device and sensors.
7. Confirm `grott_last_push` advances, compare representative power and energy values with ShinePhone, verify ShinePhone continues receiving fresh data, and confirm Home Assistant Repairs has no new Grott unit or statistics warnings.

If the add-on starts but no packets arrive, check the datalogger server setting, the Home Assistant IP address, and any firewall rule between the datalogger and port `5279`.

## MQTT

The add-on needs an MQTT broker reachable from the container. For the official Mosquitto broker add-on, the default host is usually `core-mosquitto` and the default port is `1883`.

The supported Home Assistant path is the official Mosquitto broker add-on plus the official MQTT integration with discovery enabled. This add-on publishes Home Assistant discovery topics under the default `homeassistant` discovery prefix.

If your broker requires authentication, set `mqtt_user` and `mqtt_password` in the add-on options.

The add-on publishes Home Assistant MQTT discovery through the bundled `grottext.ha` extension. Discovery config topics are retained so sensors survive Home Assistant restarts. Live state messages are not retained by default; enable `mqtt_retain` only if you understand the stale-state trade-off.

Measurement sensors such as `pvpowerout` are published without `expire_after`, so they keep their last value if the inverter stops sending fresh overnight telemetry. The `grott_last_push` timestamp sensor keeps `expire_after=900` and is the recommended freshness check.

## Collecting Logs

The add-on already runs Grott in verbose mode, so no extra debug switch is needed for normal support.

To send useful support logs:

1. Open **Settings -> Add-ons -> Grott HA Docker -> Log**.
2. Restart the add-on.
3. Wait for the failing packet or nightly problem window.
4. Copy the startup section plus the packet-flow lines around the failure.

If the issue involves short packets that were forwarded upstream but skipped locally, temporarily set:

```yaml
diagnostic_logging: true
```

restart the add-on and reproduce the problem. Treat each short-packet dump as potentially identifying device data. Never paste it into a public issue unchanged: replace stable serial/device identifiers with consistent dummy bytes and state what was redacted, or omit the raw line and ask for a private support route. Turn diagnostic logging back off afterwards because those logs can get noisy.

The most useful lines are:

- the startup summary showing the active publish path and MQTT target
- `Grott: Record blocked: ...`
- `forwarded to Growatt but not processed locally: len=... minrecl=...`
- `Short packet raw data:`
- `Record layout used : ...`
- `Grott extension processing started :  grottext.ha`
- `published ... Home Assistant discovery topics`
- `published Home Assistant state topic`

Always pseudonymise serial numbers and other stable device identifiers before sharing packet hex. Keep the record type, lengths, and layout names intact where possible, and clearly mark any substituted bytes.

## Migration From Existing Grott Add-Ons

The default `v0_1_9_standard` profile automatically reconciles Grott-owned extra discovery configs for the same device's supported generic/MOD lineage. Use the manual helper below only for stale Grott topics outside that deliberately narrow automatic scope, and always review its dry run first.

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

Before testing this candidate, create and verify the named full Home Assistant backup **Grott pre-update rollback** as described above. Without that verified backup, UAT must not begin. If UAT fails:

1. Stop `0.1.12-beta` so it releases TCP port `5279`.
2. Restore **Grott pre-update rollback** by its recorded backup ID; this is the only supported Home Assistant rollback path.
3. Confirm the restored add-on options before starting anything.
4. Start exactly one Grott listener or forwarder on port `5279`.
5. Wait for a new packet and confirm `grott_last_push` advances.
6. Compare representative Home Assistant values with ShinePhone, confirm ShinePhone continues updating, and verify Home Assistant Repairs and retained MQTT state returned to the recorded baseline.

For evidence, the independently verified previous live `0.1.11-beta` add-on manifest is:

```text
ghcr.io/herbertmt978/grott-ha-docker@sha256:4446cbcfe83c00e8c667101067fc0643afded0b6338f1d6c76ba6af113c13831
```

Both `ghcr.io/herbertmt978/grott-ha-docker:0.1.11-beta` and `:v0.1.11-beta` resolved to that four-platform manifest on 2026-07-18. The matching Docker runtime rollback manifest is `ghcr.io/herbertmt978/grott@sha256:872ca78235cd6552b26f16dcd0de17ce18288fc92ffa82e31435c7651e619b6c`.

The digest is verification evidence only; the current add-on repository does not provide historical-version reinstall as a supported rollback path.

Keep the dataloggers pointed at the same host and port only if the replacement Grott service is listening on TCP `5279`.
