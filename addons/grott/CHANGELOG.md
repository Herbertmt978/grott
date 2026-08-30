# Changelog

## Unreleased

- Enable TCP keepalive on datalogger connections accepted by `grottserver`, so a session the datalogger abandons without a FIN is reaped by the kernel instead of held ESTABLISHED indefinitely. Dataloggers allocate source ports from a small fixed pool, so each orphaned socket permanently consumed one; once the pool was exhausted the datalogger could not connect at all and went silent until `grottserver` was restarted. The probes also keep stateful firewalls from expiring a legitimately quiet session between records.

## 0.1.12 - prepared 2026-07-18

The preparation date does not by itself claim publication. Check the GitHub Releases page for availability.

- Promote the immutable `v0.1.12-beta` correction to the stable channel without changing its runtime, record-layout, Home Assistant discovery, or proxy behavior.
- Retain the reviewed three-file allowlist, protected built-in layouts, and fixture/container coverage exactly as validated in the beta.
- Make the GitHub `v0.1.12` stable release Latest when the protected release workflow completes, while keeping the Home Assistant add-on experimental because live hardware coverage remains limited to two MOD inverters.
- Carry forward the strong two-device MOD live UAT and deterministic fixture/container coverage for SPH, MIN, TL3, T060120, and generic families without presenting fixture evidence as real-hardware qualification.
- Preserve exact `v0.1.12-beta` runtime and add-on image digests as rollback evidence and retain the fork's noncommercial-use and Johan Meijer permission boundary.

## 0.1.12-beta - prepared 2026-07-18

This is the preparation date, not a claim that the beta has been published. Check the GitHub Releases page for availability.

- Replace bulk packaging of example record layouts with a reviewed three-file allowlist shared by the runtime and Home Assistant add-on images.
- Preserve Grott's built-in layouts for SPH, MIN, TL3, and T060120 packets so packaged examples cannot silently change offsets, numeric types, included fields, or dividers.
- Validate the exact final-image layout set and reject built-in overrides, duplicate external layout keys, unexpected files, malformed JSON, and loader-visible disguised JSON filenames.
- Cover unavailable inverter families with fixture/container tests while retaining the real two-inverter MOD UAT and the `v0.1.11-beta` rollback evidence.
- Record the owner's explicit waiver of the remaining observation window after the exact local candidate passed restart, two-device state publication, retained `[32, 32]` discovery, and zero-error checks; all other release gates remain required.

## 0.1.11-beta - prepared 2026-07-14

This is the preparation date, not a claim that the beta has been published. Check the GitHub Releases page for availability.

- Resolve issue #6 by making the verified 32-entity `v0_1_9_standard` Home Assistant discovery map the safe default and keeping the 171-entity MOD map opt-in through `all`.
- Preserve raw-packet handling, stable parser behavior and forwarding, and complete Home Assistant state data while applying the discovery-map change.
- Clean up retained discovery safely across generic/MOD restarts and earlier `includeall=True` maps, preserve the verified built-in generic layout, correct release metadata, and document the Home Assistant Repairs and rollback gates for this prepared candidate.
- Keep source-identical runtime and add-on images on the established non-root container path.

## 0.1.10-beta - prepared 2026-07-14

This is the preparation date, not a claim that the beta has been published. Check the GitHub Releases page for availability.

- Fix protocol-06 inverter-family selection so SPH and TL3 suffixes are applied once by the established layout selector instead of producing invalid doubled layout names.
- Reassemble protocol 02, 05, and 06 frames across TCP splits and coalesced reads, preserve valid messages before a later malformed coalesced message, isolate slow upstream connects, complete partial writes, and close late sockets deterministically during shutdown.
- Forward non-blocked raw traffic before optional local processing, exclude invalid-CRC records from local publication, and contain Influx write failures so Home Assistant processing can continue.
- Replace executable-style configuration mapping parsing with JSON and safe literal compatibility, validate final environment-over-INI values, and redact sensitive configuration from logs and errors.
- Add a pseudonymised real protocol-06 parser-output golden fixture covering frame boundaries, selected layout, normalised values, MQTT output, and the exact Home Assistant extension hand-off.
- Build source-identical runtime and Home Assistant add-on images from the same reviewed checkout with hash-locked dependencies, digest-pinned base images, and the bundled external layout JSON files Grott auto-loads from its working directory.
- Run Grott as a non-root application user, add passive health and artifact checks, support read-only filesystems, harden the supplied Compose deployment with all capabilities dropped, and retire the obsolete armv6 Dockerfile.
- Restrict the Home Assistant add-on to supported proxy mode, normalise boolean defaults so the Home Assistant extension is enabled when `ha_plugin` is omitted, disable native MQTT when the bundled extension is active, and serialise extension options as JSON.
- Harden CI and manual release publication with immutable action references, exact-source annotated tags, repeated default-branch and tag revalidation, four-platform validation, vulnerability and secret scanning, digest promotion, protected-environment gates, deterministic human-written release notes, and post-promotion verification.
- Add operator upgrade, rollback, controlled Home Assistant UAT, release-recovery, and legal-boundary guidance.

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
