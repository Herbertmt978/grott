# Grott HA Docker

[![Donate](https://img.shields.io/badge/Donate-PayPal-green.svg)](https://www.paypal.com/donate?business=RQFS46F9JTESQ&item_name=Grott+&currency_code=EUR)

Grott HA Docker is a maintained beta fork of [`johanmeijer/grott`](https://github.com/johanmeijer/grott) for people who want Growatt/ShineWiFi data in Home Assistant without relying on the Growatt cloud API.

Grott sits between your Growatt datalogger and the Growatt servers. Your datalogger sends the normal inverter packets to Grott, Grott reads them, forwards them on to Growatt, and publishes Home Assistant-friendly MQTT discovery and sensor state.

This fork keeps the upstream history intact. It exists because the problem in upstream issue [#697](https://github.com/johanmeijer/grott/issues/697) matched a real Home Assistant setup: the dataloggers were still online, but live telemetry stopped being useful in Home Assistant. This fork packages the working fix as Docker and Home Assistant add-on builds.

## Current Status

The current beta release is [`v0.1.5-beta`](https://github.com/Herbertmt978/grott/releases/tag/v0.1.5-beta).

This is beta software. It has been tested with a real ShineWiFi/SPH Home Assistant setup and with sanitized layout fixtures for generic, SPH, SPA, TL3, and MIN-style packets. Growatt has many inverter and datalogger combinations, so please treat new hardware combinations as testing until the values have been compared with ShinePhone.

Prebuilt images are published to GHCR for:

- `amd64`
- `aarch64`
- `armv7`
- `i386`

The upstream project does not currently have a repository-level license. This fork preserves attribution and does not add a license to inherited upstream code. See [docs/LEGAL.md](docs/LEGAL.md) and the upstream license discussion in [johanmeijer/grott#512](https://github.com/johanmeijer/grott/issues/512).

## What Changed In This Fork

- Home Assistant add-on packaging with prebuilt GHCR images.
- Docker images that include the Home Assistant discovery plugin.
- Guarded layout selection, so a bad family layout can be rejected instead of publishing nonsense sensors.
- Recommended proxy settings for the ShineWiFi/SPH issue: `blockcmd=True`, `time=server`, and `sendbuf=False`.
- Home Assistant MQTT discovery using [`grott-ha-plugin`](https://github.com/egguy/grott-ha-plugin).
- Cleaner entity names and unique IDs for Home Assistant.
- A dry-run-first helper for clearing stale retained MQTT discovery topics.
- Tests and sanitized packet fixtures for the layouts this fork currently knows about.

## How It Works

The recommended setup is proxy mode:

```text
Growatt datalogger -> Grott -> Growatt servers
                         |
                         +-> MQTT -> Home Assistant
```

In proxy mode, your ShinePhone app can continue to work because Grott forwards the packets to Growatt after reading them. Grott listens on TCP port `5279` by default.

Sniff mode still exists upstream, but it is harder to run safely because it needs packet routing and elevated network permissions. For Home Assistant and Docker installs, use proxy mode unless you already know you need sniffing.

## Before You Start

You need three things:

1. A Home Assistant host, Docker host, or VM that your Growatt datalogger can reach on your local network.
2. A stable IP address for that host. A DHCP reservation on your router is usually enough.
3. MQTT set up in Home Assistant.

Home Assistant will not show the sensors unless the [MQTT integration](https://www.home-assistant.io/integrations/mqtt/) is installed and connected to a broker. If you use Home Assistant OS, the easiest path is usually the official Mosquitto broker add-on plus the MQTT integration.

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
   ha_plugin: true
   mqtt_host: core-mosquitto
   mqtt_port: 1883
   mqtt_retain: false
   ```

5. If your MQTT broker requires authentication, set `mqtt_user` and `mqtt_password`.
6. Start the add-on.
7. Point your Growatt datalogger at your Home Assistant IP address on port `5279`.
8. Wait for the next datalogger packet. Many dataloggers report roughly once per minute, but some are slower.

For SPH or ShineWiFi setups affected by [#697](https://github.com/johanmeijer/grott/issues/697), set:

```yaml
invtype: sph
```

Leave `layout_strict` as `false` unless you intentionally want old Grott behaviour where the configured inverter family is forced even when the parsed values look wrong.

## Install With Docker Compose

Use this if Grott will run on a separate Linux server, NAS, or VM.

Create `docker-compose.yml`:

```yaml
services:
  grott:
    image: ghcr.io/herbertmt978/grott:0.1.5-beta
    container_name: grott
    restart: unless-stopped
    ports:
      - "5279:5279"
    volumes:
      - ./grott.ini:/app/grott.ini:ro
      - ./grott.ini:/app/config/grott.ini:ro
    environment:
      TZ: Europe/London
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

[MQTT]
nomqtt = True

[extension]
extension = True
extname = grottext.ha
extvar = {"ha_mqtt_host": "MQTT_HOST", "ha_mqtt_port": 1883, "ha_mqtt_user": "MQTT_USER", "ha_mqtt_password": "MQTT_PASSWORD", "ha_mqtt_retain": False}
```

Replace `MQTT_HOST`, `MQTT_USER`, and `MQTT_PASSWORD` with your broker details. If your broker does not require authentication, omit the user and password values from `extvar`.

For SPH or ShineWiFi setups affected by [#697](https://github.com/johanmeijer/grott/issues/697), use:

```ini
invtype = sph
```

Start Grott:

```sh
docker compose up -d
docker logs -f grott
```

`nomqtt = True` only disables Grott's older native MQTT JSON output. Home Assistant discovery and state publishing are handled by the `grottext.ha` extension in this image.

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

For Docker, change only the image first:

```yaml
image: ghcr.io/herbertmt978/grott:0.1.5-beta
```

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
- Wait for a fresh datalogger packet; discovery is usually published when Grott sees live data.

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

For Docker, change the image back to your previous tag, for example:

```yaml
image: ledidobe/grott:2.8.3_240731
```

Then restart:

```sh
docker compose up -d
```

For Home Assistant, stop this add-on and reinstall the add-on repository you used before.

The datalogger can stay pointed at the same host and port if the replacement Grott service is listening on TCP `5279`. Otherwise, change the datalogger server setting back to your previous target.

## Reporting A Problem

Please include:

- inverter model
- datalogger model
- whether you use Home Assistant add-on or Docker
- your `invtype`, `layout_strict`, and `layout_auto_family` settings
- sanitized verbose Grott packet output
- the ShinePhone values shown at roughly the same time

Do not post MQTT passwords, Growatt account credentials, API keys, or full unsanitized network captures.

## Upstream Grott

This fork exists because Grott is useful and people still depend on it. The original project, history, and most of the core parser are from [`johanmeijer/grott`](https://github.com/johanmeijer/grott). Older upstream release notes are still available in [Version_history.txt](Version_history.txt), and the upstream wiki remains useful for deeper Grott background.

If this fork helps you, please also remember that the original Grott author did the hard work of figuring out the Growatt packet flow in the first place.
