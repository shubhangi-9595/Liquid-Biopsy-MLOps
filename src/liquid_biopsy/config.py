"""Load pipeline parameters from params.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PARAMS = PROJECT_ROOT / "params.yaml"


def load_params(path: str | Path = DEFAULT_PARAMS) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def resolve(path: str | Path) -> Path:
    """Resolve a path from params.yaml relative to the project root."""
    p = Path(path)
    return p if p.is_absolute() else PROJECT_ROOT / p
