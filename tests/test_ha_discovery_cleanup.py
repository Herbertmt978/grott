from tools.ha_discovery_cleanup import cleanup_plan, discovery_pattern, extract_attribute, parse_keep


def test_discovery_pattern_targets_one_grott_device():
    assert (
        discovery_pattern("homeassistant/sensor/grott", "DL12345678")
        == "homeassistant/sensor/grott/DL12345678_+/config"
    )


def test_extract_attribute_from_valid_discovery_topic():
    assert (
        extract_attribute("homeassistant/sensor/grott/DL12345678_pvpowerout/config", "DL12345678")
        == "pvpowerout"
    )


def test_extract_attribute_ignores_other_devices_and_nested_topics():
    assert extract_attribute("homeassistant/sensor/grott/OTHER_pvpowerout/config", "DL12345678") is None
    assert (
        extract_attribute("homeassistant/sensor/grott/DL12345678_nested/value/config", "DL12345678")
        is None
    )


def test_cleanup_plan_deduplicates_and_keeps_selected_attributes():
    topics = [
        "homeassistant/sensor/grott/DL12345678_pvpowerout/config",
        "homeassistant/sensor/grott/DL12345678_pvpowerout/config",
        "homeassistant/sensor/grott/DL12345678_SOC/config",
        "homeassistant/sensor/grott/DL12345678_bad_legacy/config",
    ]
    targets = cleanup_plan(topics, "DL12345678", keep={"pvpowerout", "SOC"})
    assert [target.attribute for target in targets] == ["bad_legacy"]


def test_cleanup_plan_can_clear_all_for_a_device():
    topics = [
        "homeassistant/sensor/grott/DL12345678_pvpowerout/config",
        "homeassistant/sensor/grott/DL12345678_SOC/config",
        "homeassistant/sensor/grott/OTHER_SOC/config",
    ]
    targets = cleanup_plan(topics, "DL12345678", clear_all=True)
    assert [target.attribute for target in targets] == ["SOC", "pvpowerout"]


def test_parse_keep_trims_empty_values():
    assert parse_keep("pvpowerout, SOC,,") == {"pvpowerout", "SOC"}
