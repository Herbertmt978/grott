# Grott HA Docker

Grott HA Docker is a maintained Home Assistant-focused fork of [`johanmeijer/grott`](https://github.com/johanmeijer/grott) for people who want Growatt/ShineWiFi data in Home Assistant without relying on the Growatt cloud API.

Grott sits between your Growatt datalogger and the Growatt servers. Your datalogger sends the normal inverter packets to Grott, Grott reads them, forwards them on to Growatt, and publishes Home Assistant-friendly MQTT discovery and sensor state.

This fork keeps the upstream history intact. It exists because the problem in upstream issue [#697](https://github.com/johanmeijer/grott/issues/697) matched a real Home Assistant setup: the dataloggers were still online, but live telemetry stopped being useful in Home Assistant. This fork packages the working fix as Docker and Home Assistant add-on builds.

## Current Status

The current stable release line is `v0.1.12`, which promotes the external-layout packaging correction tested in `v0.1.12-beta` without changing runtime or layout behavior. `v0.1.12` is the repository Latest release when its matching release entry is present. `v0.1.12-beta` remains an immutable historical prerelease, alongside the earlier `v0.1.10-beta` and `v0.1.11-beta` records. On 2026-07-18, after the exact beta candidate remained healthy through a restart, published 164 successful Home Assistant states across both MOD devices, retained `[32, 32]` discovery entities, and recorded zero runtime errors, the owner explicitly waived the remaining observation window. That decision does not waive any PR, hosted-CI, protected-branch, tag, environment, image-validation, or release gate in [RELEASING.md](RELEASING.md). Read the [human-written release notes](docs/releases/v0.1.12.md) for the problem, evidence, and promotion boundary. The [GitHub releases listing](https://github.com/Herbertmt978/grott/releases) is the public reference. The Releases page is the supported-availability authority: absence means a candidate is unsupported, but does not prove that GHCR tags are absent because image promotion can precede release creation. Every image is supported only when its matching release entry exists.

| Version domain | Version | Meaning |
| --- | --- | --- |
| Fork/add-on release | `0.1.12` | The current stable Docker release and matching Home Assistant add-on metadata. |
| Bundled Grott core (upstream startup version) | `2.8.3` | The version printed by the inherited Grott entry point; fork fixes are carried on top of that core. |
| Bundled Home Assistant extension | `0.0.8` | The in-repository `grott_ha.py` extension used for MQTT discovery and state. |

The Home Assistant add-on catalog stage remains `experimental`. This release has been tested with two real ShineWiFi/MOD inverters and with sanitized layout fixtures for generic, SPH, SPA, TL3, MIN, and T060120-style packets. Fixture/container coverage is not real-hardware validation for those other families, so treat new hardware combinations as testing until values have been compared with ShinePhone.

Published images are available on GHCR. The `0.1.12` image targets the same platform set and is supported only when its matching release entry exists on this repository's Releases page; publication remains subject to every gate in [RELEASING.md](RELEASING.md):

- `amd64`
- `aarch64`
- `armv7`
- `i386`

The upstream project does not currently have a repository-level license. This fork preserves attribution and does not add a license to inherited upstream code. On 2026-07-14, the fork owner confirmed that upstream redistribution permission has been obtained for this fork and its container images. That permission does not authorize commercial use or reuse unless Johan Meijer has separately agreed, and any financial reward or appreciation is directed to him. Public release still requires every gate in [RELEASING.md](RELEASING.md); see [docs/LEGAL.md](docs/LEGAL.md) and the upstream discussion in [johanmeijer/grott#512](https://github.com/johanmeijer/grott/issues/512).

## What Changed In This Fork

- Home Assistant add-on packaging with prebuilt GHCR images.
- Docker images that include the bundled Home Assistant discovery extension.
- Guarded layout selection, so a bad family layout can be rejected instead of publishing nonsense sensors.
- Recommended proxy settings for the ShineWiFi/SPH issue: `blockcmd=True`, `time=server`, and `sendbuf=False`.
- Home Assistant MQTT discovery using the bundled `grottext.ha` extension with the official Mosquitto broker add-on and official MQTT integration in discovery mode.
- Cleaner entity names and unique IDs for Home Assistant.
- A dry-run-first helper for clearing stale retained MQTT discovery topics.
- Tests and sanitized packet fixtures for the layouts this fork currently knows about.
- A reviewed three-file external-layout allowlist and final-image semantic checks that keep packaged examples from overriding Grott's built-in SPH, MIN, TL3, and T060120 layouts.
- Frame-aware proxy handling for TCP records that arrive split or coalesced, with slow connection attempts isolated from healthy sessions.
- Safe configuration mapping parsing, redacted environment diagnostics, and validation of the final effective settings.
- Source-identical, hash-locked Docker/add-on builds with non-root execution, read-only-filesystem support, passive health checks, and four-platform validation.

## How It Works

The recommended setup is proxy mode:

```text
Growatt datalogger -> Grott -> Growatt servers
                         |
                         +-> MQTT -> Home Assistant
```

In proxy mode, your ShinePhone app can continue to work because Grott forwards the packets to Growatt after reading them. Grott listens on TCP port `5279` by default.

Sniff mode still exists upstream, but it needs packet routing and elevated network permissions. The v0.1.12 images and supplied Compose profile are qualified for proxy mode only. If an existing installation uses `server` or `sniff`, keep that installation in place; this release does not provide a supported packaged migration for those modes.

## Before You Start

You need three things:

1. A Home Assistant host, Docker host, or VM that your Growatt datalogger can reach on your local network.
2. A stable IP address for that host. A DHCP reservation on your router is usually enough.
3. The official Home Assistant MQTT integration set up in discovery mode.

Home Assistant will not show the sensors unless the official [MQTT integration](https://www.home-assistant.io/integrations/mqtt/) is installed and connected to a broker with MQTT discovery enabled.

If you use Home Assistant OS, this add-on is configured around the official [Mosquitto broker add-on](https://github.com/home-assistant/addons/blob/master/mosquitto/DOCS.md) and the official MQTT integration in discovery mode. The default add-on options use `mqtt_host: core-mosquitto` and `mqtt_port: 1883` for that setup.

MQTT discovery should stay enabled. Home Assistant enables MQTT discovery by default, and this fork uses it to create the Grott devices and sensors automatically.

## Install As A Home Assistant Add-On

This is the easiest install if Grott will run on the same Home Assistant machine.

1. In Home Assistant, open **Settings -> Add-ons -> Add-on Store**.
2. Open the store menu, choose **Repositories**, and add:

   ```text
   https://github.com/Herbertmt978/grott
   ```

3. Install **Grott HA Docker**.
4. Check the add-on configuration.

   The defaults are intended to be conservative:

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

   These defaults assume the official Mosquitto broker add-on and the official MQTT integration with discovery enabled.

   The Home Assistant add-on supports proxy mode only. When `ha_plugin` is enabled, the bundled Home Assistant extension is the MQTT publisher and Grott's separate native MQTT publisher is disabled to avoid duplicate publishing paths.

5. If your MQTT broker requires authentication, set `mqtt_user` and `mqtt_password`.
6. Start the add-on.
7. Point your Growatt datalogger at your Home Assistant IP address on port `5279`.
8. Wait for the next datalogger packet. Many dataloggers report roughly once per minute, but some are slower.

For SPH or ShineWiFi setups affected by [#697](https://github.com/johanmeijer/grott/issues/697), set:

```yaml
invtype: sph
```

Leave `layout_strict` as `false` unless you intentionally want old Grott behaviour where the configured inverter family is forced even when the parsed values look wrong.

## Choose The Home Assistant Entity Profile

`ha_entity_profile` controls Home Assistant MQTT discovery only. It accepts exactly these values:

- `v0_1_9_standard` is the default for the `T06NNNNXMOD` and `T06NNNNX` decoded layouts. Its 31 layout-derived entities plus `grott_last_push` reproduce the 32 Home Assistant entities discovered by the verified standard-Docker `v0.1.9-beta` setup.
- `all` exposes the complete active `T06NNNNXMOD` map: 170 layout-derived entities plus `grott_last_push`, for 171 discovered entities. Generic `T06NNNNX` remains at the verified 32 entities with the normal `includeall=False` parser setting. If Grott's separate `includeall=True` setting is enabled, `all` also discovers fields marked `incl: no`: 205 entities for MOD and 36 for generic. Other decoded layouts retain their existing discovery behavior under either profile.

Changing this profile does not change Grott packet parsing, validation, or proxy forwarding. The full Home Assistant state JSON is unchanged: it still contains every normalized decoded packet value plus `grott_last_push`; the profile filters discovery only. Grott's native raw telemetry is also unchanged. In the supported Home Assistant extension setup, native MQTT remains disabled with `gnomqtt=True` exactly as before; the extension continues to publish discovery and state.

The default profile also reconciles retained discovery. It first publishes the desired retained configs at QoS 1, then sends retained empty QoS 1 tombstones only for known Grott config topics in the same device's supported generic/MOD lineage that fall outside the verified 32-entity set. This includes extras left by an earlier MOD `all` or `includeall=True` run even when the first post-upgrade packet is generic. It does not scan the broker or remove another device's or integration's discovery configs. If cleanup fails, Grott logs it, still publishes the full state, and retries the exact cleanup on the next packet. Because an omitted option defaults to `v0_1_9_standard`, upgrading an existing installation triggers this cleanup on its next live packet.

Create and verify your backup before upgrading or switching profiles. Retained discovery configs, entity-registry changes, dashboard customizations, and statistics repairs are not restored merely by rolling back an image. Every Home Assistant deployment, including Docker-backed Home Assistant, needs a full Home Assistant backup that covers its registry/configuration plus retained MQTT state through the included Mosquitto data or a separate broker snapshot. Docker users must also keep their previous `grott.ini` and immutable image reference. After rollback, verify the expected entity set, `grott_last_push`, and the Home Assistant Repairs page before accepting the result.

To opt into the complete distinct layout map in the add-on, change only this option:

```yaml
ha_entity_profile: all
```

The examples use placeholders and contain no real or stable device identifiers.

## Install With Docker Compose

Use this if Grott will run on a separate Linux server, NAS, or VM.

**Availability check:** use the repository image below only when `v0.1.12` appears on this repository's Releases page. Until that entry exists, the image is unsupported even if a GHCR tag is temporarily visible during promotion. The hardened settings are qualified with the matching `0.1.12` image and should not be assumed to work unchanged with an older image.

Create `docker-compose.yml`:

```yaml
services:
  grott:
    image: ghcr.io/herbertmt978/grott:0.1.12
    container_name: grott
    restart: unless-stopped
    init: true
    user: "10001:10001"
    read_only: true
    tmpfs:
      - /tmp
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
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

Create `grott.ini`:

```ini
[Generic]
mode = proxy
blockcmd = True
time = server
sendbuf = False
invtype = default
layout_strict = False
layout_auto_family = True
diagnostic_logging = False

[MQTT]
nomqtt = True

[extension]
extension = True
extname = grottext.ha
extvar = {"ha_mqtt_host": "MQTT_HOST", "ha_mqtt_port": 1883, "ha_mqtt_user": "MQTT_USER", "ha_mqtt_password": "MQTT_PASSWORD", "ha_mqtt_retain": False, "ha_entity_profile": "v0_1_9_standard"}
```

Replace `MQTT_HOST`, `MQTT_USER`, and `MQTT_PASSWORD` with your broker details. If your broker does not require authentication, omit the user and password values from `extvar`.

To opt into the complete distinct layout map with `grott.ini`, use this exact `extvar` form instead:

```ini
extvar = {"ha_mqtt_host": "MQTT_HOST", "ha_mqtt_port": 1883, "ha_mqtt_user": "MQTT_USER", "ha_mqtt_password": "MQTT_PASSWORD", "ha_mqtt_retain": False, "ha_entity_profile": "all"}
```

The maintained image runs as UID/GID `10001:10001`. On a Linux Docker host, the bind-mounted `grott.ini` must be readable by UID `10001`; a restrictive example is `sudo chown 10001:10001 grott.ini && sudo chmod 0600 grott.ini`. Do not make a secret-bearing configuration file world-readable just to satisfy the container.

The Compose profile keeps the image root filesystem read-only. If you intentionally use Grott's optional file output (`-o`) or a file-writing extension, first prefer a dedicated writable bind mount and point output into that mount. If the output path cannot be redirected, `read_only: false` is the explicit escape hatch; document the reason, keep capabilities dropped, and restore the read-only setting when the file output is retired.

For SPH or ShineWiFi setups affected by [#697](https://github.com/johanmeijer/grott/issues/697), use:

```ini
invtype = sph
```

Start Grott:

```sh
docker compose up -d
docker logs -f grott
```

`nomqtt = True` only disables Grott's older native MQTT JSON output. Home Assistant discovery and state publishing are handled by the bundled `grottext.ha` extension in this image.

The maintained Docker image already starts Grott with verbose logging enabled. For normal support logs, restart the container, reproduce the problem, and send the startup lines plus the packet-flow lines around the failure:

```sh
docker logs --since 15m grott
```

If you use Compose:

```sh
docker compose logs --since=15m grott
```

The most useful lines are the startup publish-path summary, any `Record blocked` line, any `forwarded to Growatt but not processed locally` line, the selected record layout, and the `grottext.ha` publish lines.

If you are chasing new short mystery packets such as `0119` or `0118`, temporarily enable:

```ini
diagnostic_logging = True
```

under `[Generic]`, restart Grott, and reproduce the problem. Treat every `Short packet raw data:` line as potentially identifying device data: never paste it into a public issue unchanged. Replace stable serial/device identifiers with consistent dummy bytes and say what was redacted, or omit the raw line and ask for a private support route. Leave diagnostic logging disabled in normal use because the dumps can also get noisy.

Home Assistant measurement sensors such as `pvpowerout` are published without `expire_after`, so they keep their last value if the inverter stops sending fresh overnight telemetry. Use the `grott_last_push` sensor as the freshness indicator instead.

## If Your Datalogger Already Points At Home Assistant

Some ShineWiFi-X devices are already configured to send data to the Home Assistant IP address. If you want to move Grott into Docker on another VM, NAS, or Linux host, you do not have to reconfigure the datalogger straight away.

Use [`HA Forwarder`](https://github.com/Herbertmt978/HA_Forwarder) on Home Assistant to listen on port `5279` and forward that TCP traffic to the machine running Grott:

```text
ShineWiFi-X -> Home Assistant HA Forwarder -> Grott Docker VM -> Growatt servers
                                             |
                                             +-> MQTT -> Home Assistant
```

In HA Forwarder, set:

```yaml
listen_port: 5279
target_host: "GROTT_DOCKER_HOST"
target_port: 5279
```

Replace `GROTT_DOCKER_HOST` with the hostname or IP address of the VM running the Grott Docker container. This is useful when Home Assistant is the stable address your dataloggers already know, but you prefer to run Grott somewhere else.

## Configure The Growatt Datalogger

Your datalogger must be told to send data to Grott instead of sending directly to Growatt. In proxy mode, Grott forwards the packet to Growatt after reading it, so ShinePhone should continue to receive data.

The exact screens vary between ShineLan, ShineWiFi-X, ShineWiFi-S, firmware versions, and phone apps. The clearest illustrated guide I found is the datalogger setup guide from [`muppet3000/homeassistant-grott`](https://github.com/muppet3000/homeassistant-grott/blob/main/docs/setup/datalogger.md). The short version is below.

### ShineLan

1. Find the datalogger IP address from your router or DHCP server.
2. Open that IP address in a browser.
3. Sign in. Common defaults are `admin` / `admin`, or `admin` with the CC code printed on the device.
4. Open the network settings page.
5. Turn domain resolution off if the page offers a setting such as `ResolvDomain`.
6. Set the server IP to the IP address of the machine running Grott.
7. Keep or set the server port to `5279` if there is a port field.
8. Optionally lower the data transfer interval. One minute is a common minimum.
9. Save and wait for the next packet.

### ShineWiFi-X / ShineWiFi-S

1. Open the ShinePhone app.
2. Go to **Me -> Datalogger Configuration**.
3. Add or select the datalogger by scanning its QR code or entering the serial and verification code.
4. Choose hotspot mode.
5. Press the datalogger button to enter hotspot mode. The blue LED should stay on.
6. Connect your phone to the Wi-Fi network broadcast by the datalogger.
7. Return to ShinePhone and open the advanced settings.
8. Open server settings.
9. Unlock the server settings. Many ShineWiFi devices use a password in the format `growattYYYYMMDD`, using today's date.
10. Turn off domain-name mode.
11. Set the server IP to the IP address of the machine running Grott.
12. Keep or set the server port to `5279` if there is a port field.
13. Save, then choose the option to configure immediately.
14. Wait a minute or two for the datalogger to reconnect and send a packet.

If you later remove Grott, put the datalogger server setting back to Growatt's server or to the service you were using before.

## Check That It Is Working

After the datalogger is pointed at Grott, check the Grott log first.

You want to see:

- a Growatt packet received
- a layout selected
- decoded values such as `pvserial`, `pvpowerin`, `pvpowerout`, or `epvtotal`
- Home Assistant discovery/state publishing through `grottext.ha`

Then check Home Assistant:

1. Open **Settings -> Devices & services**.
2. Make sure the [MQTT integration](https://www.home-assistant.io/integrations/mqtt/) is present and connected.
3. Open the MQTT integration and look for Grott devices.
4. Search entities for your inverter or datalogger serial.

Useful first sensors to check are:

- `Grott last data push`
- `PV Input (Actual)`
- `PV Output (Actual)`
- `Lifetime solar energy`
- `Inverter temperature`

Compare a few values with ShinePhone. They will not always update at the exact same second, but the numbers should be believable and in the same ballpark.

## Migrating From Another Grott Install

Back up your existing `grott.ini` or Home Assistant add-on options first.

The default `v0_1_9_standard` profile automatically reconciles Grott-owned extra discovery configs for the same device's supported generic/MOD lineage. Use the manual helper below only for stale Grott topics outside that deliberately narrow automatic scope, and always review its dry run first.

For Docker, change only the image first:

```yaml
    image: ghcr.io/herbertmt978/grott:0.1.12
```

Only make that change after the release is declared supported on the Releases page. Before then it is unsupported, even if a failed publication attempt left a GHCR tag behind.

Then make sure your config includes the proxy settings:

```ini
mode = proxy
blockcmd = True
time = server
sendbuf = False
```

For Home Assistant add-on users, install this repository, copy your MQTT settings into the add-on options, and start the add-on.

If a previous install created broken or stale MQTT discovery entities, dry-run the cleanup helper before deleting anything:

```sh
python tools/ha_discovery_cleanup.py --host MQTT_HOST --device DATALOGGER_SERIAL --all
```

Only run the destructive version after reading the topic list:

```sh
python tools/ha_discovery_cleanup.py --host MQTT_HOST --device DATALOGGER_SERIAL --all --execute
```

The helper only targets Grott discovery topics under:

```text
homeassistant/sensor/grott/
```

## Troubleshooting

If Home Assistant shows no Grott sensors:

- Confirm the MQTT integration is installed and connected.
- Confirm MQTT discovery is enabled.
- Confirm Grott can reach the MQTT broker.
- Check the Grott log for `grottext.ha`.
- Check `grott_last_push` if you need to see whether Grott is still receiving fresh telemetry. Power sensors are intentionally allowed to keep their last value overnight.
- Wait for a fresh datalogger packet; discovery is usually published when Grott sees live data.

If you need logs for a support issue:

- Home Assistant add-on users should open **Settings -> Add-ons -> Grott HA Docker -> Log**, restart the add-on, and copy the startup lines plus the packet-flow lines around the failure.
- Docker users should restart the container and run `docker logs --since 15m grott` or `docker compose logs --since=15m grott`.
- If the issue involves short forwarded-only packets, temporarily enable `diagnostic_logging = True` in `[Generic]` first.
- Please include the active publish-path summary, blocked-record lines, short-record lines, the selected layout, and `grottext.ha` publish lines. Include `Short packet raw data:` only after pseudonymising stable serial/device identifiers; never post raw packet hex unchanged.

If Grott receives no packets:

- Confirm the datalogger server IP is the Grott host IP.
- Confirm the datalogger is using port `5279`.
- Confirm the Grott host firewall allows inbound TCP `5279`.
- Confirm the datalogger and Grott host are on networks that can reach each other.

If sensors are created but values are wrong:

- Compare the values with ShinePhone at the same time.
- Try setting `invtype` to your inverter family, such as `sph`, `spf`, `tl3`, `spa`, or `min`.
- Keep `layout_strict = False` while testing.
- Open a layout request and include sanitized verbose Grott output plus the matching ShinePhone values.

If ShinePhone stops updating:

- Make sure Grott is running in `proxy` mode.
- Check that Grott can reach the Growatt server from its network.
- Check the Grott log for connection errors after packet parsing.

## Rollback

Back up the active `grott.ini` before a Docker update. Before any Home Assistant UAT, including Docker-backed Home Assistant, create a full Home Assistant backup named **Grott pre-update rollback**, wait for it to report completed, verify it is visible and covers the entity registry/configuration, and retain its backup ID. Preserve retained MQTT discovery through that backup when it includes Mosquitto, or take a separate broker snapshot. Without that verified recovery set, UAT must not begin. Also record which process owns TCP port `5279`; only one Grott service or forwarder may listen there during rollback.

For this release, the verified previous live fork image is the immutable `0.1.12-beta`. Pinning its exact four-platform manifest is the most precise Docker rollback:

```yaml
image: ghcr.io/herbertmt978/grott@sha256:066d806774a147bc4c448761d026eb831cdcfa29bc32ef3a1c361a36a2ea361a
```

The equivalent readable tag is `ghcr.io/herbertmt978/grott:0.1.12-beta`; both `0.1.12-beta` and `v0.1.12-beta` were verified to resolve to that manifest on 2026-07-18.

Then restart:

```sh
docker compose up -d
```

For Home Assistant, the only supported rollback is the verified **Grott pre-update rollback** backup: stop the candidate, restore that named full backup by its recorded ID, and confirm the restored add-on options before starting anything. Start exactly one Grott listener or forwarder on TCP `5279`.

For evidence, the previous `0.1.12-beta` add-on manifest was independently verified as `ghcr.io/herbertmt978/grott-ha-docker@sha256:410f2b2e4dfe810aa1d9d8b8591eaae0852ae9f61486d78f663cd6a95c2ab6f1`; the digest does not make historical add-on installation from the current repository a supported rollback mechanism.

After either rollback, wait for a fresh packet, confirm `grott_last_push` advances, compare several power and energy values with ShinePhone, confirm ShinePhone itself still receives new data through the proxy path, and verify Home Assistant Repairs has no new Grott unit or statistics warnings.

The datalogger can stay pointed at the same host and port if the replacement Grott service is listening on TCP `5279`. Otherwise, change the datalogger server setting back to your previous target.

## Reporting A Problem

Please include:

- inverter model
- datalogger model
- whether you use Home Assistant add-on or Docker
- your `invtype`, `layout_strict`, and `layout_auto_family` settings
- whether `diagnostic_logging` was enabled
- sanitized verbose Grott packet output
- the ShinePhone values shown at roughly the same time

Do not post MQTT passwords, Growatt account credentials, API keys, full unsanitized network captures, unchanged packet hex, or stable device identifiers.

## Upstream Grott

This fork exists because Grott is useful and people still depend on it. The original project, history, and most of the core parser are from [`johanmeijer/grott`](https://github.com/johanmeijer/grott). Older upstream release notes are still available in [Version_history.txt](Version_history.txt), and the upstream wiki remains useful for deeper Grott background.

If this fork helps you, please also remember that the original Grott author did the hard work of figuring out the Growatt packet flow in the first place.
