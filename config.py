"""Application configuration helpers for NE FRESH.

Step 3 extraction: environment parsing and Flask configuration live here so
app_core.py no longer owns deployment/runtime configuration details.
"""
from __future__ import annotations

import os
import secrets
from datetime import timedelta
from typing import Callable

from dotenv import load_dotenv

load_dotenv()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _env_text(name: str, default: str = "") -> str:
    return (os.getenv(name) or default or "").strip()


def _is_production_env() -> bool:
    raw = (
        os.getenv("APP_ENV")
        or os.getenv("FLASK_ENV")
        or os.getenv("ENV")
        or ""
    ).strip().lower()
    return raw in {"production", "prod", "live"}




def _env_int(name: str, default: int = 0, minimum: int = 0, maximum: int = 10) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return int(default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return int(default)
    return max(minimum, min(maximum, value))


def get_trusted_proxy_config() -> dict:
    """Return the explicit ProxyFix trust configuration.

    ProxyFix must only trust the number of proxies actually in front of the app.
    Production EC2 deployment uses one local Nginx reverse proxy.
    """
    enabled = _env_bool("TRUST_PROXY_HEADERS", False)
    return {
        "enabled": enabled,
        "x_for": _env_int("PROXY_FIX_X_FOR", 1 if enabled else 0, 0, 3),
        "x_proto": _env_int("PROXY_FIX_X_PROTO", 1 if enabled else 0, 0, 3),
        "x_host": _env_int("PROXY_FIX_X_HOST", 1 if enabled else 0, 0, 3),
        "x_port": _env_int("PROXY_FIX_X_PORT", 1 if enabled else 0, 0, 3),
        "x_prefix": _env_int("PROXY_FIX_X_PREFIX", 0, 0, 3),
    }


def get_api_cors_origins():
    raw = (os.getenv("CORS_ORIGINS") or "").strip()
    if raw:
        if raw == "*":
            return "*"
        return [origin.strip() for origin in raw.split(",") if origin.strip()]
    return [
        "http://localhost:5000",
        "http://127.0.0.1:5000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]


def configure_application(app, log_warning: Callable[..., None] = print) -> None:
    """Apply deployment-safe Flask/session configuration without registering routes."""
    app_secret = (os.getenv("APP_SECRET_KEY") or os.getenv("SECRET_KEY") or "").strip()
    if not app_secret:
        if _is_production_env():
            raise RuntimeError("APP_SECRET_KEY or SECRET_KEY must be set in the production server environment.")
        app_secret = secrets.token_urlsafe(48)
        log_warning(
            "[SECURITY WARNING] APP_SECRET_KEY/SECRET_KEY is not set. "
            "Using a temporary runtime key; sessions will reset on restart."
        )
    elif len(app_secret) < 32:
        log_warning(
            "[SECURITY WARNING] APP_SECRET_KEY/SECRET_KEY is shorter than recommended. "
            "Use at least 32 random characters in production."
        )
    app.secret_key = app_secret

    session_cookie_httponly = _env_bool("SESSION_COOKIE_HTTPONLY", False)
    if _is_production_env() and not session_cookie_httponly:
        log_warning(
            "[SECURITY WARNING] SESSION_COOKIE_HTTPONLY=false. "
            "Set SESSION_COOKIE_HTTPONLY=true in production unless WebView requires JS-readable cookies."
        )
    app.config["SESSION_COOKIE_HTTPONLY"] = session_cookie_httponly

    session_cookie_secure = _env_bool("SESSION_COOKIE_SECURE", _is_production_env())
    if _is_production_env() and not session_cookie_secure:
        log_warning(
            "[SECURITY WARNING] SESSION_COOKIE_SECURE=false. "
            "Set SESSION_COOKIE_SECURE=true on HTTPS production."
        )

    session_cookie_samesite = (os.getenv("SESSION_COOKIE_SAMESITE") or "").strip()
    if not session_cookie_samesite:
        session_cookie_samesite = "None" if session_cookie_secure else "Lax"
    elif session_cookie_samesite.lower() == "none" and not session_cookie_secure:
        log_warning(
            "[SECURITY WARNING] SESSION_COOKIE_SAMESITE=None requires SESSION_COOKIE_SECURE=true. "
            "Falling back to Lax for local/http login compatibility."
        )
        session_cookie_samesite = "Lax"

    app.config["SESSION_COOKIE_SECURE"] = session_cookie_secure
    app.config["SESSION_COOKIE_SAMESITE"] = session_cookie_samesite
    app.config["SESSION_COOKIE_NAME"] = "session"
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)
    app.config["ENABLE_CSRF_PROTECTION"] = _env_bool("ENABLE_CSRF_PROTECTION", True)


def warn_missing_production_sender_settings(log_warning: Callable[..., None] = print) -> None:
    """Warn about unsafe/incomplete production SMTP settings without exposing secrets."""
    if not _is_production_env():
        return

    required = ["SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM"]
    values = {name: (os.getenv(name) or "").strip() for name in required}
    missing = [name for name in required if not values[name]]
    if missing:
        log_warning(
            "[PRODUCTION WARNING] Email/OTP sender is not fully configured. Missing: "
            + ", ".join(missing)
        )
        return

    try:
        smtp_port = int((os.getenv("SMTP_PORT") or "587").strip())
    except (TypeError, ValueError):
        log_warning("[PRODUCTION WARNING] SMTP_PORT is not a valid integer.")
        return

    smtp_use_ssl = _env_bool("SMTP_USE_SSL", smtp_port == 465)
    smtp_use_tls = _env_bool("SMTP_USE_TLS", not smtp_use_ssl)
    smtp_debug = _env_bool("SMTP_DEBUG", False)
    smtp_host = values["SMTP_HOST"].lower()

    if smtp_use_ssl and smtp_use_tls:
        log_warning(
            "[PRODUCTION WARNING] SMTP_USE_SSL and SMTP_USE_TLS cannot both be enabled."
        )

    if smtp_host == "smtp.gmail.com":
        if smtp_port == 587 and (not smtp_use_tls or smtp_use_ssl):
            log_warning(
                "[PRODUCTION WARNING] Gmail port 587 requires STARTTLS: "
                "SMTP_USE_TLS=true and SMTP_USE_SSL=false."
            )
        elif smtp_port == 465 and (not smtp_use_ssl or smtp_use_tls):
            log_warning(
                "[PRODUCTION WARNING] Gmail port 465 requires SSL: "
                "SMTP_USE_SSL=true and SMTP_USE_TLS=false."
            )
        elif smtp_port not in {465, 587}:
            log_warning(
                "[PRODUCTION WARNING] Gmail SMTP should use port 587 (STARTTLS) or 465 (SSL)."
            )

        if values["SMTP_USER"].lower() != values["SMTP_FROM"].lower():
            log_warning(
                "[PRODUCTION WARNING] SMTP_USER and SMTP_FROM differ. This is valid only "
                "when the authenticated Google account has the From address configured as an approved send-as alias."
            )

    if smtp_debug:
        log_warning(
            "[PRODUCTION WARNING] SMTP_DEBUG=true is not recommended in production. "
            "Raw SMTP protocol debugging is disabled by the sender to protect credentials."
        )
