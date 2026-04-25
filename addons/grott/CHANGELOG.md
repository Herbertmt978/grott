# Changelog

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
