import pytest

import grottconf
from grottconf import Conf, parse_mapping


def load_ini(tmp_path, text):
    config = tmp_path / "grott.ini"
    config.write_text(text, encoding="utf-8")
    conf = Conf.__new__(Conf)
    conf.cfgfile = str(config)
    conf.pvinverters = 1
    conf.procconf()
    return conf


def load_effective_influx_config(tmp_path, text):
    config = tmp_path / "grott.ini"
    config.write_text(text, encoding="utf-8")
    conf = Conf.__new__(Conf)
    conf.cfgfile = str(config)
    conf.verbose = False
    conf.pvinverters = 1
    conf.influx2 = False
    conf.ifip = "localhost"
    conf.procconf()
    conf.procenv()
    return conf


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("gmqttpassword", "mqtt-secret-sentinel"),
        ("gpvapikey", "pv-secret-sentinel"),
        ("gifpassword", "influx-password-sentinel"),
        ("giftoken", "influx-token-sentinel"),
        (
            "gextvar",
            '{"ha_mqtt_host":"broker.local","ha_mqtt_password":"nested-secret-sentinel"}',
        ),
    ],
)
def test_getenv_verbose_reports_supply_without_value(monkeypatch, capsys, name, value):
    conf = Conf.__new__(Conf)
    conf.verbose = True
    monkeypatch.setenv(name, value)

    assert conf.getenv(name) == value

    output = capsys.readouterr().out
    assert name in output
    assert "supplied" in output.lower()
    assert value not in output
    assert "secret-sentinel" not in output


@pytest.mark.parametrize("name", ["ginvtypemap", "gextvar"])
def test_environment_mapping_rejects_executable_expression(monkeypatch, tmp_path, name):
    marker = tmp_path / "executed"
    expression = (
        f"__import__('pathlib').Path({str(marker)!r}).write_text('executed') or {{}}"
    )
    conf = Conf.__new__(Conf)
    conf.verbose = False
    conf.pvinverters = 1
    conf.extvar = {}
    monkeypatch.setenv(name, expression)

    with pytest.raises(ValueError, match=rf"{name}.*JSON object.*dictionary") as error:
        conf.procenv()

    assert not marker.exists()
    assert expression not in str(error.value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ('{"SPH": "sph"}', {"SPH": "sph"}),
        ("{'SPH': 'sph'}", {"SPH": "sph"}),
    ],
)
def test_parse_mapping_preserves_json_and_python_literal_dicts(value, expected):
    assert parse_mapping(value, "test mapping") == expected


@pytest.mark.parametrize("value", ['["not", "a", "mapping"]', "('not', 'a', 'mapping')"])
def test_parse_mapping_rejects_non_dictionary_shapes(value):
    with pytest.raises(ValueError, match="must be a JSON object.*dictionary"):
        parse_mapping(value, "test mapping")


def test_ini_mapping_settings_accept_json_and_python_literals(tmp_path):
    config = tmp_path / "grott.ini"
    config.write_text(
        "[Generic]\n"
        'invtypemap = {"SPH": "sph"}\n'
        "[extension]\n"
        "extvar = {'ha_mqtt_host': 'broker.local'}\n",
        encoding="utf-8",
    )
    conf = Conf.__new__(Conf)
    conf.cfgfile = str(config)
    conf.pvinverters = 1

    conf.procconf()

    assert conf.invtypemap == {"SPH": "sph"}
    assert conf.extvar == {"ha_mqtt_host": "broker.local"}


def test_environment_numeric_settings_remain_integers(monkeypatch):
    conf = Conf.__new__(Conf)
    conf.verbose = False
    conf.pvinverters = 1
    conf.minrecl = 100
    conf.grottport = 5279
    conf.valueoffset = 6
    conf.growattport = 5279
    conf.mqttport = 1883
    conf.ifport = 8086
    monkeypatch.setenv("gminrecl", "120")
    monkeypatch.setenv("ggrottport", "15279")
    monkeypatch.setenv("gvalueoffset", "8")
    monkeypatch.setenv("ggrowattport", "25279")
    monkeypatch.setenv("gmqttport", "2883")
    monkeypatch.setenv("gifport", "18086")

    conf.procenv()

    assert conf.minrecl == 120
    assert conf.grottport == 15279
    assert conf.valueoffset == 8
    assert conf.growattport == 25279
    assert conf.mqttport == 2883
    assert conf.ifport == 18086
    assert all(
        isinstance(value, int)
        for value in (
            conf.minrecl,
            conf.grottport,
            conf.valueoffset,
            conf.growattport,
            conf.mqttport,
            conf.ifport,
        )
    )


@pytest.mark.parametrize(
    ("name", "value", "limit"),
    [
        ("gminrecl", "256", "0 and 255"),
        ("ggrottport", "70000", "0 and 65535"),
        ("gvalueoffset", "not-a-number", "0 and 255"),
    ],
)
def test_invalid_environment_numeric_settings_fail_actionably(
    monkeypatch, name, value, limit
):
    conf = Conf.__new__(Conf)
    conf.verbose = False
    conf.pvinverters = 1
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=rf"{name}.*integer between {limit}"):
        conf.procenv()


def test_ini_valueoffset_remains_an_integer(tmp_path):
    config = tmp_path / "grott.ini"
    config.write_text("[Generic]\nvalueoffset = 8\n", encoding="utf-8")
    conf = Conf.__new__(Conf)
    conf.cfgfile = str(config)
    conf.pvinverters = 1

    conf.procconf()

    assert conf.valueoffset == 8
    assert isinstance(conf.valueoffset, int)


@pytest.mark.parametrize(
    ("name", "attribute", "hostname"),
    [
        ("ggrottip", "grottip", "collector.local"),
        ("ggrowattip", "growattip", "server.growatt.com"),
        ("gmqttip", "mqttip", "core-mosquitto"),
        ("gifip", "ifip", "influxdb"),
    ],
)
def test_environment_hosts_accept_dns_and_service_names(
    monkeypatch, name, attribute, hostname
):
    conf = Conf.__new__(Conf)
    conf.verbose = False
    conf.pvinverters = 1
    setattr(conf, attribute, "original.example")
    monkeypatch.setenv(name, hostname)

    conf.procenv()

    assert getattr(conf, attribute) == hostname


@pytest.mark.parametrize(
    ("section", "source"),
    [
        ("Generic", "Generic.ip"),
        ("Growatt", "Growatt.ip"),
        ("MQTT", "MQTT.ip"),
        ("influx", "influx.ip"),
    ],
)
def test_ini_empty_hosts_fail_actionably(tmp_path, section, source):
    config = tmp_path / "grott.ini"
    config.write_text(f"[{section}]\nip = \n", encoding="utf-8")
    conf = Conf.__new__(Conf)
    conf.cfgfile = str(config)
    conf.pvinverters = 1

    with pytest.raises(
        ValueError, match=rf"{source}.*non-empty IP address.*hostname"
    ):
        conf.procconf()


def test_empty_environment_host_fails_actionably(monkeypatch):
    conf = Conf.__new__(Conf)
    conf.verbose = False
    conf.pvinverters = 1
    monkeypatch.setenv("gmqttip", "  ")

    with pytest.raises(
        ValueError, match=r"gmqttip.*non-empty IP address.*hostname"
    ):
        conf.procenv()


def test_ini_generic_default_preserves_proxy_bind_sentinel(tmp_path):
    conf = load_ini(tmp_path, "[Generic]\nip = default\n")

    assert conf.grottip == "default"


@pytest.mark.parametrize(
    ("section", "attribute", "alias"),
    [
        ("Generic", "grottip", "grott_proxy"),
        ("Growatt", "growattip", "growatt_cloud_proxy"),
        ("MQTT", "mqttip", "core_mosquitto"),
        ("influx", "ifip", "influx_db"),
    ],
)
def test_ini_hosts_accept_docker_service_aliases_with_underscores(
    tmp_path, section, attribute, alias
):
    conf = load_ini(tmp_path, f"[{section}]\nip = {alias}\n")

    assert getattr(conf, attribute) == alias


def test_ini_host_accepts_numeric_service_alias(tmp_path):
    conf = load_ini(tmp_path, "[MQTT]\nip = 123\n")

    assert conf.mqttip == "123"


@pytest.mark.parametrize(
    ("section", "attribute", "address"),
    [
        ("Generic", "grottip", "192.0.2.10"),
        ("Growatt", "growattip", "server.growatt.com"),
        ("MQTT", "mqttip", "2001:db8::10"),
        ("influx", "ifip", "2001:db8::20"),
    ],
)
def test_ini_hosts_preserve_valid_ip_and_dns_forms(
    tmp_path, section, attribute, address
):
    conf = load_ini(tmp_path, f"[{section}]\nip = {address}\n")

    assert getattr(conf, attribute) == address


@pytest.mark.parametrize(
    "url",
    [
        "http://influx_db",
        "https://influx.example.test:8443",
        "http://influx.example.test:8086/api/v2",
        "https://[2001:db8::30]:8443/influx",
    ],
)
def test_ini_influx2_accepts_http_urls_without_changing_the_value(tmp_path, url):
    conf = load_ini(
        tmp_path,
        f"[influx]\ninflux2 = true\nip = {url}\nport = 8086\n",
    )

    assert conf.ifip == url


def test_environment_can_enable_influx2_for_an_ini_url(monkeypatch, tmp_path):
    monkeypatch.setenv("ginflux2", "true")

    conf = load_effective_influx_config(
        tmp_path,
        "[influx]\nip = http://influx_db/api/v2\n",
    )

    assert conf.influx2 == "true"
    assert conf.ifip == "http://influx_db/api/v2"


def test_environment_disabling_influx2_rejects_an_ini_url(monkeypatch, tmp_path):
    monkeypatch.setenv("ginflux2", "false")

    with pytest.raises(ValueError, match=r"influx.ip.*http\(s\) URL.*enabled"):
        load_effective_influx_config(
            tmp_path,
            "[influx]\ninflux2 = true\nip = http://influx_db/api/v2\n",
        )


def test_environment_host_override_wins_when_influx2_is_disabled(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("ginflux2", "false")
    monkeypatch.setenv("gifip", "influx_db")

    conf = load_effective_influx_config(
        tmp_path,
        "[influx]\ninflux2 = true\nip = http://old-influx/api/v2\n",
    )

    assert conf.influx2 == "false"
    assert conf.ifip == "influx_db"


def test_invalid_environment_influx_override_reports_its_source(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("ginflux2", "true")
    monkeypatch.setenv("gifip", "ftp://influx_db")

    with pytest.raises(ValueError, match=r"^gifip.*http\(s\) URL.*enabled$"):
        load_effective_influx_config(tmp_path, "[influx]\nip = influx_ini\n")


@pytest.mark.parametrize(
    ("address", "port", "expected"),
    [
        ("influx_db", 8086, "influx_db:8086"),
        ("2001:db8::20", 8086, "[2001:db8::20]:8086"),
        ("http://influx_db", 8086, "http://influx_db:8086"),
        (
            "https://influx.example.test:8443",
            8086,
            "https://influx.example.test:8443",
        ),
        (
            "http://influx.example.test:8086/api/v2",
            9999,
            "http://influx.example.test:8086/api/v2",
        ),
        (
            "https://[2001:db8::30]/influx",
            8443,
            "https://[2001:db8::30]:8443/influx",
        ),
    ],
)
def test_influx2_client_url_places_the_configured_port_in_the_authority(
    address, port, expected
):
    assert grottconf.format_influx2_url(address, port) == expected


@pytest.mark.parametrize(
    ("section", "settings", "source"),
    [
        ("Generic", "ip = 127.0.0.1; touch owned", "Generic.ip"),
        ("Generic", "ip = 999.999.999.999", "Generic.ip"),
        ("Growatt", "ip = https://server.growatt.com", "Growatt.ip"),
        ("MQTT", "ip = broker.example.test:1883", "MQTT.ip"),
        ("MQTT", "ip = broker example.test", "MQTT.ip"),
        ("MQTT", "ip = core_mosquitto_", "MQTT.ip"),
        ("influx", "influx2 = false\nip = http://influxdb", "influx.ip"),
        ("influx", "influx2 = true\nip = ftp://influxdb", "influx.ip"),
        ("influx", "influx2 = true\nip = http://influxdb:99999", "influx.ip"),
        (
            "influx",
            "influx2 = true\nip = http://user:secret@influxdb",
            "influx.ip",
        ),
        (
            "influx",
            "influx2 = true\nip = http://influxdb/api/$(touch-owned)",
            "influx.ip",
        ),
    ],
)
def test_ini_host_settings_reject_expression_url_and_port_syntax(
    tmp_path, section, settings, source
):
    with pytest.raises(ValueError, match=source):
        load_ini(tmp_path, f"[{section}]\n{settings}\n")


@pytest.mark.parametrize(
    ("section", "value", "source"),
    [
        ("Generic", "70000", "Generic.port"),
        ("Growatt", "-1", "Growatt.port"),
        ("MQTT", "not-a-port", "MQTT.port"),
        ("influx", "65536", "influx.port"),
    ],
)
def test_ini_ports_reject_malformed_or_out_of_range_values(
    tmp_path, section, value, source
):
    with pytest.raises(
        ValueError, match=rf"{source}.*integer between 0 and 65535"
    ):
        load_ini(tmp_path, f"[{section}]\nport = {value}\n")


@pytest.mark.parametrize(
    ("section", "attribute"),
    [
        ("Generic", "grottport"),
        ("Growatt", "growattport"),
        ("MQTT", "mqttport"),
        ("influx", "ifport"),
    ],
)
def test_ini_ports_remain_integers(tmp_path, section, attribute):
    conf = load_ini(tmp_path, f"[{section}]\nport = 18086\n")

    assert getattr(conf, attribute) == 18086
    assert isinstance(getattr(conf, attribute), int)
