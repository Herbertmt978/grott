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
  mqtt_host="$(json_get mqtt_host core-mosquitto)"
  mqtt_port="$(json_get mqtt_port 1883)"
  mqtt_user="$(json_get mqtt_user '')"
  mqtt_password="$(json_get mqtt_password '')"
  export gextension=True
  export gextname=grott_ha
  if [ -n "${mqtt_user}" ]; then
    export gextvar="{\"ha_mqtt_host\":\"${mqtt_host}\",\"ha_mqtt_port\":${mqtt_port},\"ha_mqtt_user\":\"${mqtt_user}\",\"ha_mqtt_password\":\"${mqtt_password}\"}"
  else
    export gextvar="{\"ha_mqtt_host\":\"${mqtt_host}\",\"ha_mqtt_port\":${mqtt_port}}"
  fi
fi

exec python -u /app/grott.py -v
