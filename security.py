"""HTTP security hooks shared by all NE FRESH route modules."""
from __future__ import annotations

import html
import secrets

from flask import abort, current_app, jsonify, request, session
from flask_cors import CORS
from markupsafe import Markup
from werkzeug.middleware.proxy_fix import ProxyFix

from config import get_api_cors_origins, get_trusted_proxy_config

CSRF_EXEMPT_PATH_PREFIXES = ("/api/", "/static/")


def _get_csrf_token():
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def _inject_csrf_helpers():
    def csrf_field():
        token = html.escape(_get_csrf_token(), quote=True)
        return Markup(f'<input type="hidden" name="csrf_token" value="{token}">')

    return {"csrf_token": _get_csrf_token(), "csrf_field": csrf_field}


def _protect_html_form_posts():
    if not current_app.config.get("ENABLE_CSRF_PROTECTION", True):
        return None
    if request.method in ["GET", "HEAD", "OPTIONS", "TRACE"]:
        return None

    path = request.path or ""
    if any(path.startswith(prefix) for prefix in CSRF_EXEMPT_PATH_PREFIXES):
        return None

    if request.is_json and request.headers.get("Authorization"):
        return None

    expected = session.get("_csrf_token")
    received = (
        request.form.get("csrf_token")
        or request.form.get("_csrf_token")
        or request.headers.get("X-CSRFToken")
        or request.headers.get("X-CSRF-Token")
        or ""
    )

    if not expected or not received or not secrets.compare_digest(str(expected), str(received)):
        if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
            return jsonify({"ok": False, "error": "Security token expired. Please refresh and try again."}), 400
        abort(400, description="Security token expired. Please refresh and try again.")
    return None


def add_no_cache_headers(resp):
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp



def register_trusted_proxy(app) -> None:
    """Trust forwarded headers only when explicitly configured.

    The production EC2 topology binds Gunicorn to localhost and places exactly
    one Nginx proxy in front of it.  Keeping this disabled by default prevents
    clients from spoofing X-Forwarded-* headers during local/direct execution.
    """
    cfg = get_trusted_proxy_config()
    if not cfg.get("enabled"):
        return

    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=cfg["x_for"],
        x_proto=cfg["x_proto"],
        x_host=cfg["x_host"],
        x_port=cfg["x_port"],
        x_prefix=cfg["x_prefix"],
    )


def register_security(app) -> None:
    origins = get_api_cors_origins()
    CORS(
        app,
        resources={
            r"/api/*": {
                "origins": origins,
                "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
                "allow_headers": ["Content-Type", "Authorization"],
                "supports_credentials": bool(origins != "*"),
            }
        },
    )
    app.context_processor(_inject_csrf_helpers)
    app.before_request(_protect_html_form_posts)
    app.after_request(add_no_cache_headers)
