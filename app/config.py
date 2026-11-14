"""Settings read from env vars (and .env locally).

AQ_TOKEN is only for the scheduler's MCP calls. It is never logged, returned by
any endpoint, or sent to the browser.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

load_dotenv(ROOT / ".env")  # real env vars take precedence


@dataclass(frozen=True)
class Settings:
    live_mode: bool
    data_dir: Path
    as_of: str | None
    aq_token: str = field(repr=False)  # repr=False keeps it out of logs and tracebacks


def _bool(value):
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def get_settings():
    """Read settings now (not at import), so tests can change env vars."""
    return Settings(
        live_mode=_bool(os.environ.get("LIVE_MODE", "false")),
        data_dir=Path(os.environ.get("DATA_DIR", "./data")),
        as_of=os.environ.get("AS_OF") or None,
        aq_token=os.environ.get("AQ_TOKEN", ""),
    )
