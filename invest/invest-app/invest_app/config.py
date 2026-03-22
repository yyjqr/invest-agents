"""Application configuration and paths."""

from pathlib import Path

# Default data directory: ~/invest-app-data
DATA_DIR = Path.home() / "invest-app-data"
PORTFOLIO_FILE = DATA_DIR / "portfolio.json"


def ensure_data_dir() -> Path:
    """Create the data directory if it doesn't exist."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR
