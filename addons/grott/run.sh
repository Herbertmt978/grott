#!/usr/bin/env sh
set -eu

OPTIONS=/data/options.json

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

if [ "$(json_get ha_plugin true)" = "True" ]; then
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

print(repr(payload))
PY
)"
fi

exec python -u /app/grott.py -v
