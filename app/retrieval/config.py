"""Load config.yaml: $RETRIEVAL_CONFIG if set, otherwise config.yaml at the repository root.

Any "${NAME}" in a value is replaced with the environment variable NAME, so secrets
(DB passwords) come from the environment / Secret Manager, never the file.
"""

import os
import re
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"

_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _env_value(match: re.Match) -> str:
    name = match.group(1)
    value = os.getenv(name)
    if not value:
        raise ValueError(f"config references ${{{name}}} but environment variable {name} is not set")
    return value


def _expand_env(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, str):
        return _ENV_REF.sub(_env_value, value)
    return value


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    path = Path(path or os.getenv("RETRIEVAL_CONFIG") or DEFAULT_CONFIG_PATH)
    with path.open(encoding="utf-8") as handle:
        return _expand_env(yaml.safe_load(handle) or {})
