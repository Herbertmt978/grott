import importlib.util
from pathlib import Path


def resolve_plugin_path(base_path: Path | None = None) -> Path:
    root = (base_path or Path(__file__).resolve().parents[1]).resolve()
    candidates = (
        root / "examples" / "Home Assistent" / "grott_ha.py",
        root / "grott_ha.py",
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("Could not locate grott_ha.py for the local HA extension")


_PLUGIN_PATH = resolve_plugin_path()
_SPEC = importlib.util.spec_from_file_location("grott_ha_example", _PLUGIN_PATH)
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

__version__ = getattr(_MODULE, "__version__", "unknown")
MqttStateHandler = _MODULE.MqttStateHandler
grottext = _MODULE.grottext
make_payload = _MODULE.make_payload
mapping = _MODULE.mapping
normalize_key = _MODULE.normalize_key
normalize_values = _MODULE.normalize_values
publish_multiple = _MODULE.publish_multiple
publish_single = _MODULE.publish_single
state_topic = _MODULE.state_topic
