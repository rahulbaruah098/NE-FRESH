"""Upload configuration and file-type validation."""
from __future__ import annotations

import os

from config import _is_production_env
from logging_config import log_warning

ALLOWED_EXTS = {"jpg", "jpeg", "png", "webp"}


def configure_uploads(app) -> None:
    configured = (os.getenv("UPLOAD_FOLDER") or os.getenv("NELOCALS_UPLOAD_FOLDER") or "").strip()
    app.config["UPLOAD_FOLDER"] = (
        os.path.abspath(configured)
        if configured
        else os.path.join(os.path.dirname(__file__), "uploads")
    )
    if _is_production_env() and not configured:
        log_warning(
            "[PRODUCTION WARNING] UPLOAD_FOLDER is not set. Using local ./uploads; "
            "configure a persistent upload path in production to prevent file loss."
        )
    app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTS
