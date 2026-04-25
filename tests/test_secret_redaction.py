from grottdata import redact_sensitive


def test_redact_sensitive_masks_nested_secret_keys():
    value = {
        "ha_mqtt_host": "mqtt.local",
        "ha_mqtt_password": "live-password",
        "nested": {
            "token": "live-token",
            "safe": "visible",
        },
        "items": [{"X-Pvoutput-Apikey": "pv-key"}],
    }

    assert redact_sensitive(value) == {
        "ha_mqtt_host": "mqtt.local",
        "ha_mqtt_password": "**secret**",
        "nested": {
            "token": "**secret**",
            "safe": "visible",
        },
        "items": [{"X-Pvoutput-Apikey": "**secret**"}],
    }
