"""Application configuration and paths."""

import json
import os
from pathlib import Path

# Default data directory: ~/invest-app-data
DATA_DIR = Path.home() / "invest-app-data"
PORTFOLIO_FILE = DATA_DIR / "portfolio.json"

# Global API-keys configuration file (shared across all users)
_API_KEYS_FILE = DATA_DIR / "api_keys.json"


def ensure_data_dir() -> Path:
    """Create the data directory if it doesn't exist."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


# ---------------------------------------------------------------------------
# API key helpers — read priority: env var → api_keys.json
# ---------------------------------------------------------------------------

def _load_api_keys() -> dict:
    if _API_KEYS_FILE.exists():
        try:
            return json.loads(_API_KEYS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _get_key(env_names: tuple[str, ...], json_fields: tuple[str, ...]) -> str:
    """Return API key from environment variables, falling back to api_keys.json."""
    for env_name in env_names:
        value = os.environ.get(env_name, "").strip()
        if value:
            return value

    stored = _load_api_keys()
    for json_field in json_fields:
        value = str(stored.get(json_field, "")).strip()
        if value:
            return value
    return ""


def get_alpha_vantage_key() -> str:
    """Return Alpha Vantage API key (env ALPHA_VANTAGE_KEY or api_keys.json)."""
    return _get_key(("ALPHA_VANTAGE_KEY",), ("alpha_vantage",))


def get_tavily_key() -> str:
    """Return Tavily API key (env TAVILY_API_KEY or api_keys.json)."""
    return _get_key(("TAVILY_API_KEY",), ("tavily",))


def get_qveris_key() -> str:
    """Return qveris.ai API key, keeping backward compatibility with old names."""
    return _get_key(
        ("QVERIS_API_KEY", "QVERS_API_KEY"),
        ("qveris", "qvers"),
    )


def get_qvers_key() -> str:
    """Backward-compatible alias for older code paths."""
    return get_qveris_key()


def save_api_keys(**kwargs: str) -> None:
    """Persist one or more API keys to api_keys.json (keys with empty value are removed)."""
    ensure_data_dir()
    current = _load_api_keys()
    updates = dict(kwargs)
    if "qvers" in updates and "qveris" not in updates:
        updates["qveris"] = updates["qvers"]
    if "qveris" in updates:
        updates["qvers"] = ""

    for field, value in updates.items():
        if value:
            current[field] = value
        else:
            current.pop(field, None)
    _API_KEYS_FILE.write_text(
        json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8"
    )
