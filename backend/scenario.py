import json
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCENARIO = ROOT / "config" / "port_scenario.json"


def _deep_merge(base, overlay):
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = deepcopy(value)
    return base


def load_scenario(path=DEFAULT_SCENARIO, variant="disruption"):
    """Load a scenario and apply one of its named demo variants."""
    with Path(path).open(encoding="utf-8") as handle:
        scenario = json.load(handle)

    variants = scenario.pop("scenario_variants", {})
    if variant not in variants:
        raise ValueError(f"unknown scenario variant: {variant}")
    overlay = deepcopy(variants[variant])
    shipment_overrides = overlay.pop("shipment_overrides", {})
    truck_overrides = overlay.pop("truck_overrides", {})
    selected = _deep_merge(deepcopy(scenario), overlay)
    for shipment in selected.get("shipments", []):
        _deep_merge(shipment, shipment_overrides.get(shipment["id"], {}))
    for truck in selected.get("trucks", []):
        _deep_merge(truck, truck_overrides.get(truck["id"], {}))
    return selected


def available_scenarios(path=DEFAULT_SCENARIO):
    with Path(path).open(encoding="utf-8") as handle:
        scenario = json.load(handle)
    return [
        {"id": key, "label": value.get("name", key.replace("-", " ").title())}
        for key, value in scenario.get("scenario_variants", {}).items()
    ]
