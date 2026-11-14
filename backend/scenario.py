import json
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCENARIO = ROOT / "config" / "port_scenario.json"


def load_scenario(path=DEFAULT_SCENARIO):
    """Load and return an isolated scenario dictionary."""
    with Path(path).open(encoding="utf-8") as handle:
        return deepcopy(json.load(handle))
