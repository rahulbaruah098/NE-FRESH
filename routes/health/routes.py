"""Lightweight liveness/readiness endpoints for production process managers.

These routes deliberately avoid application business logic.  Liveness proves
that the Flask worker can answer HTTP requests.  Readiness additionally checks
the current MongoDB dependency and writable local upload storage used by the
existing application.
"""

import os

from flask import jsonify

from app_core import app, mongo


@app.route("/health/live", methods=["GET"], endpoint="health_live")
def health_live():
    return jsonify({"ok": True, "status": "alive"}), 200


@app.route("/health/ready", methods=["GET"], endpoint="health_ready")
def health_ready():
    checks = {
        "mongo": "ok",
        "uploads": "ok",
    }
    ready = True

    try:
        mongo.command("ping")
    except Exception:
        checks["mongo"] = "unavailable"
        ready = False

    upload_folder = app.config.get("UPLOAD_FOLDER")
    if not upload_folder or not os.path.isdir(upload_folder) or not os.access(upload_folder, os.W_OK):
        checks["uploads"] = "unavailable"
        ready = False

    payload = {
        "ok": ready,
        "status": "ready" if ready else "not_ready",
        "checks": checks,
    }
    return jsonify(payload), 200 if ready else 503
