#!/usr/bin/env sh
set -eu

OPTIONS="${OPTIONS:-/data/options.json}"
GROTT_RUNNER="${GROTT_RUNNER:-python}"

json_get() {
  python - "$OPTIONS" "$1" "$2" <<'PY'
import json
import sys

path, key, default = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
except FileNotFoundError:
    data = {}
value = data.get(key, default)
if isinstance(value, bool):
    print("True" if value else "False")
elif isinstance(value, str) and value.strip().lower() in {"true", "false"}:
    print("True" if value.strip().lower() == "true" else "False")
else:
    print(value)
PY
}

export gmode="$(json_get mode proxy)"
export gblockcmd="$(json_get blockcmd true)"
export gtime="$(json_get time server)"
export gsendbuf="$(json_get sendbuf false)"
export ginvtype="$(json_get invtype default)"
export glayoutstrict="$(json_get layout_strict false)"
export glayoutautofamily="$(json_get layout_auto_family true)"
export gdiagnosticlogging="$(json_get diagnostic_logging false)"

if [ "$(json_get ha_plugin true)" = "True" ]; then
  export gnomqtt=True
  export gextension=True
  export gextname=grottext.ha
  export gextvar="$(python - "$OPTIONS" <<'PY'
import json
import sys

path = sys.argv[1]
try:
    with open(path, "r", encoding="utf-8") as handle:
        options = json.load(handle)
except FileNotFoundError:
    options = {}

payload = {
    "ha_mqtt_host": options.get("mqtt_host", "core-mosquitto"),
    "ha_mqtt_port": int(options.get("mqtt_port", 1883)),
    "ha_mqtt_retain": bool(options.get("mqtt_retain", False)),
}
if options.get("mqtt_user"):
    payload["ha_mqtt_user"] = options.get("mqtt_user")
    payload["ha_mqtt_password"] = options.get("mqtt_password", "")

print(json.dumps(payload, separators=(",", ":")))
PY
)"
fi

if [ "$(id -u)" -eq 0 ]; then
  exec su-exec grott:grott "$GROTT_RUNNER" -u /app/grott.py -v
fi

exec "$GROTT_RUNNER" -u /app/grott.py -v
