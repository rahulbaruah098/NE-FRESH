"""Small compatibility logging layer for NE FRESH.

This remains intentionally lightweight in Step 3. Later deployment phases can
attach structured handlers without changing business-code call sites.
"""
from __future__ import annotations

import os


def is_debug_logging_enabled() -> bool:
    def enabled(name: str) -> bool:
        return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}
    return enabled("NEFRESH_DEBUG_LOGS") or enabled("FLASK_DEBUG")


def log_debug(*args) -> None:
    if is_debug_logging_enabled():
        print(*args)


def log_warning(*args) -> None:
    print(*args)
