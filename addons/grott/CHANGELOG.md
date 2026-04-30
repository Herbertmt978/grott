# Changelog

## 0.1.9-beta

- Add a `diagnostic_logging` support switch that dumps hex for short packets which were forwarded to Growatt but skipped locally because they were below `minrecl`.
- Surface the new support switch in the Home Assistant add-on options, Docker/example config, and support docs so users can send usable short-packet evidence without enabling it permanently.

## 0.1.8-beta

- Stop publishing `expire_after` on measurement sensors such as `pvpowerout` so Home Assistant keeps the last value overnight instead of forcing `unavailable` after 15 minutes without fresh telemetry.
- Keep `expire_after=900` on `grott_last_push` so it remains a freshness indicator when Grott stops receiving live packets.
- Remove the external `grott-ha-plugin` runtime dependency from the Docker image path and copy the in-repo `grottext` package into the image instead.

## 0.1.7-beta

- Add explicit diagnostic logs for blocked records so it is clear they were stopped before forwarding to Growatt and before local publish.
- Add explicit diagnostic logs for short records that were forwarded upstream but skipped locally because they were below `minrecl`.
- Surface the effective publish path in verbose startup output so add-on users can distinguish native MQTT from the `grottext.ha` extension path.
- Add Home Assistant extension publish logs for discovery-topic count and state-topic success.
- Vendor a local `grottext.ha` package shim so the add-on runtime uses the repository-tested HA extension code path.

## 0.1.6-beta

- Skip low-confidence inverter records before MQTT, PVOutput, InfluxDB, or Home Assistant extension publishing.
- Keep smart-meter records on the older tolerant path while inverter layouts are guarded by `layout_min_score`.

## 0.1.5-beta

- Redact extension variables, PVOutput API keys, and other secret-shaped values from verbose logs.
- Keep the README pointed at the latest beta images after the logging hardening.

## 0.1.4-beta

- Fix the Docker runtime image so the installed `grottext.ha` package is not shadowed by the old sample `grottext.py` extension.

## 0.1.3-beta

- Use `grott-ha-plugin` for Home Assistant MQTT discovery so Docker and add-on installs share the same entity naming as working HA deployments.
- Generate add-on MQTT extension settings safely from options, including the optional `mqtt_retain` setting.
- Add issue templates and first-run, rollback, and beta notes for wider testing.

## 0.1.2-beta

- Add temporary compiler tooling during Docker image builds so dependencies can build on architectures without wheels.
- Publish usable prebuilt GHCR images for Docker and the Home Assistant add-on.

Note: `0.1.1-beta` was removed from the remote tags because the multi-architecture image publish failed before a usable release existed.

## 0.1.1-beta

- Attempt prebuilt GHCR images for Docker and the Home Assistant add-on.
- Add isolated Home Assistant add-on repository validation.
- Add retained MQTT discovery cleanup helper with dry-run defaults.
- Add sanitized SPH, SPA, TL3, and MIN layout fixtures.
- Document the upstream licensing gap before wider promotion.

## 0.1.0-beta

- Add guarded layout selection for Growatt extended records.
- Add generic fallback for `0103` records.
- Sanitize Home Assistant MQTT discovery keys.
- Package Grott as a Docker image and Home Assistant add-on from the same fork source.
