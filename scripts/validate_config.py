#!/usr/bin/env python3
"""Validate NE FRESH environment configuration without importing the Flask app.

The command never prints secret values.  In production mode it exits non-zero
for settings that would make the process unsafe or unusable behind Nginx/
Gunicorn. Optional integrations are warnings unless partially configured.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")

TRUE_VALUES = {"1", "true", "yes", "on", "y"}
FALSE_VALUES = {"0", "false", "no", "off", "n"}
PLACEHOLDER_RE = re.compile(r"(change[-_ ]?me|replace[-_ ]?me|example|your[-_ ]|<.+>|xxx+)", re.I)


def env_text(name: str) -> str:
    return (os.getenv(name) or "").strip()


def env_bool(name: str, default: bool | None = None):
    raw = env_text(name).lower()
    if not raw:
        return default
    if raw in TRUE_VALUES:
        return True
    if raw in FALSE_VALUES:
        return False
    return None


def is_production() -> bool:
    raw = (env_text("APP_ENV") or env_text("FLASK_ENV") or env_text("ENV")).lower()
    return raw in {"production", "prod", "live"}


def looks_placeholder(value: str) -> bool:
    return bool(value and PLACEHOLDER_RE.search(value))


def validate(force_production: bool = False):
    production = force_production or is_production()
    errors: list[str] = []
    warnings: list[str] = []
    notes: list[str] = []

    app_env = env_text("APP_ENV") or env_text("FLASK_ENV") or env_text("ENV")
    if production and app_env.lower() not in {"production", "prod", "live"}:
        errors.append("APP_ENV must be set to production (or prod/live) for a production preflight.")

    secret = env_text("APP_SECRET_KEY") or env_text("SECRET_KEY")
    if production:
        if not secret:
            errors.append("APP_SECRET_KEY or SECRET_KEY is required in production.")
        elif len(secret) < 32 or looks_placeholder(secret):
            errors.append("APP_SECRET_KEY/SECRET_KEY must be a non-placeholder random value of at least 32 characters.")
    elif not secret:
        warnings.append("No APP_SECRET_KEY/SECRET_KEY is set; development sessions will reset when the process restarts.")

    mongo_uri = env_text("MONGO_URI")
    if production and not mongo_uri:
        errors.append("MONGO_URI is required in production.")
    elif mongo_uri and looks_placeholder(mongo_uri):
        errors.append("MONGO_URI still appears to contain placeholder/example text.")
    elif mongo_uri and "localhost" in mongo_uri.lower() and production:
        warnings.append("Production MONGO_URI points to localhost; confirm this is intentional and that MongoDB backups/access controls are configured.")

    cors = env_text("CORS_ORIGINS")
    if production and not cors:
        errors.append("CORS_ORIGINS is required in production so /api/* does not fall back to localhost-only origins.")
    elif production and cors == "*":
        errors.append("CORS_ORIGINS='*' is not accepted for production credentialed API usage; list exact HTTPS origins.")
    elif cors:
        origins = [item.strip() for item in cors.split(",") if item.strip()]
        insecure = [origin for origin in origins if origin.startswith("http://") and "localhost" not in origin and "127.0.0.1" not in origin]
        if production and insecure:
            warnings.append("One or more production CORS origins use plain HTTP; use HTTPS unless there is an intentional private-network exception.")

    secure_cookie = env_bool("SESSION_COOKIE_SECURE", None)
    if production and secure_cookie is not True:
        errors.append("SESSION_COOKIE_SECURE=true is required behind production HTTPS.")

    httponly_cookie = env_bool("SESSION_COOKIE_HTTPONLY", None)
    if production and httponly_cookie is not True:
        warnings.append("SESSION_COOKIE_HTTPONLY is not true. Keep this only if the mobile WebView genuinely requires JavaScript-readable session cookies.")

    samesite = env_text("SESSION_COOKIE_SAMESITE")
    if samesite and samesite.lower() not in {"lax", "strict", "none"}:
        errors.append("SESSION_COOKIE_SAMESITE must be Lax, Strict or None.")
    if production and samesite.lower() == "none" and secure_cookie is not True:
        errors.append("SESSION_COOKIE_SAMESITE=None requires SESSION_COOKIE_SECURE=true.")

    csrf = env_bool("ENABLE_CSRF_PROTECTION", True)
    if production and csrf is not True:
        errors.append("ENABLE_CSRF_PROTECTION must remain enabled in production unless an approved replacement exists.")

    trust_proxy = env_bool("TRUST_PROXY_HEADERS", False)
    if production and trust_proxy is not True:
        errors.append("TRUST_PROXY_HEADERS=true is required for the supported Nginx -> Gunicorn production topology.")

    for proxy_name in [
        "PROXY_FIX_X_FOR",
        "PROXY_FIX_X_PROTO",
        "PROXY_FIX_X_HOST",
        "PROXY_FIX_X_PORT",
        "PROXY_FIX_X_PREFIX",
    ]:
        raw_proxy = env_text(proxy_name)
        if not raw_proxy:
            continue
        try:
            proxy_count = int(raw_proxy)
            if proxy_count < 0 or proxy_count > 3:
                errors.append(f"{proxy_name} must be between 0 and 3.")
        except ValueError:
            errors.append(f"{proxy_name} must be an integer proxy count.")

    if production and trust_proxy is True:
        expected_single_proxy = ["PROXY_FIX_X_FOR", "PROXY_FIX_X_PROTO", "PROXY_FIX_X_HOST", "PROXY_FIX_X_PORT"]
        for proxy_name in expected_single_proxy:
            if env_text(proxy_name) not in {"1"}:
                warnings.append(f"{proxy_name} should be 1 for the supported single-Nginx EC2 topology.")

    gunicorn_bind = env_text("GUNICORN_BIND")
    if production and gunicorn_bind and not (
        gunicorn_bind.startswith("127.0.0.1:") or gunicorn_bind.startswith("localhost:") or gunicorn_bind.startswith("unix:")
    ):
        errors.append("GUNICORN_BIND must remain loopback/unix-only in production; Nginx is the public listener.")

    if production and env_bool("FLASK_DEBUG", False):
        errors.append("FLASK_DEBUG must be disabled in production.")
    if production and env_bool("NEFRESH_AUTO_RELOAD", False):
        errors.append("NEFRESH_AUTO_RELOAD must be disabled in production.")
    if production and env_bool("NEFRESH_DEBUG_LOGS", False):
        warnings.append("NEFRESH_DEBUG_LOGS is enabled in production; disable unless temporarily diagnosing a controlled issue.")

    upload_folder = env_text("UPLOAD_FOLDER") or env_text("NELOCALS_UPLOAD_FOLDER")
    if production and not upload_folder:
        errors.append("UPLOAD_FOLDER (or NELOCALS_UPLOAD_FOLDER) is required in production so runtime media is not stored inside the release directory.")
    elif upload_folder:
        resolved = Path(upload_folder).expanduser()
        if not resolved.is_absolute():
            if production:
                errors.append("UPLOAD_FOLDER must be an absolute persistent path in production.")
            else:
                warnings.append("UPLOAD_FOLDER is relative; production should use an absolute persistent path.")
        else:
            try:
                root = ROOT_DIR.resolve()
                target = resolved.resolve()
                if production and (target == root or root in target.parents):
                    errors.append("UPLOAD_FOLDER points inside the application release directory; use persistent EBS/S3-backed storage outside releases.")
            except OSError:
                pass

    smtp_names = ["SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM"]
    smtp_values = {name: env_text(name) for name in smtp_names}

    # SMTP_HOST may be prefilled as smtp.gmail.com in example files, so actual
    # sender configuration is considered started only when a credential/from
    # field is present. Once started, all required values must be complete.
    smtp_started = any(smtp_values[name] for name in ["SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM"])

    if smtp_started:
        missing = [name for name in smtp_names if not smtp_values[name]]
        if missing:
            errors.append("SMTP configuration is partial; missing: " + ", ".join(missing))
        else:
            smtp_host = smtp_values["SMTP_HOST"].lower()

            port_raw = env_text("SMTP_PORT") or "587"
            try:
                smtp_port = int(port_raw)
                if smtp_port < 1 or smtp_port > 65535:
                    errors.append("SMTP_PORT must be between 1 and 65535.")
            except ValueError:
                smtp_port = None
                errors.append("SMTP_PORT must be a valid integer.")

            smtp_tls = env_bool("SMTP_USE_TLS", True)
            smtp_ssl = env_bool("SMTP_USE_SSL", False)
            smtp_debug = env_bool("SMTP_DEBUG", False)

            if env_text("SMTP_USE_TLS") and smtp_tls is None:
                errors.append("SMTP_USE_TLS must be true or false.")
            if env_text("SMTP_USE_SSL") and smtp_ssl is None:
                errors.append("SMTP_USE_SSL must be true or false.")
            if env_text("SMTP_DEBUG") and smtp_debug is None:
                errors.append("SMTP_DEBUG must be true or false.")

            if smtp_tls is True and smtp_ssl is True:
                errors.append("SMTP_USE_TLS and SMTP_USE_SSL cannot both be true.")

            if "@" not in smtp_values["SMTP_USER"]:
                errors.append("SMTP_USER must be a valid email address.")
            if "@" not in smtp_values["SMTP_FROM"]:
                errors.append("SMTP_FROM must be a valid sender email address.")

            if looks_placeholder(smtp_values["SMTP_USER"]):
                errors.append("SMTP_USER still appears to contain placeholder/example text.")
            if looks_placeholder(smtp_values["SMTP_PASSWORD"]):
                errors.append("SMTP_PASSWORD still appears to contain placeholder/example text.")
            if looks_placeholder(smtp_values["SMTP_FROM"]):
                errors.append("SMTP_FROM still appears to contain placeholder/example text.")

            if smtp_host == "smtp.gmail.com" and smtp_port is not None:
                if smtp_port == 587:
                    if smtp_tls is not True or smtp_ssl is True:
                        errors.append(
                            "Gmail SMTP port 587 requires SMTP_USE_TLS=true and SMTP_USE_SSL=false."
                        )
                elif smtp_port == 465:
                    if smtp_ssl is not True or smtp_tls is True:
                        errors.append(
                            "Gmail SMTP port 465 requires SMTP_USE_SSL=true and SMTP_USE_TLS=false."
                        )
                else:
                    errors.append("Gmail SMTP must use port 587 (STARTTLS) or 465 (SSL).")

                if smtp_values["SMTP_USER"].lower() != smtp_values["SMTP_FROM"].lower():
                    warnings.append(
                        "SMTP_USER and SMTP_FROM differ. Confirm the authenticated Google account has "
                        "SMTP_FROM configured as an approved send-as alias."
                    )
            elif smtp_host:
                warnings.append(
                    "SMTP_HOST is not smtp.gmail.com. The mail service is provider-neutral, but the current "
                    "OTP migration is intended to use Gmail/Google Workspace SMTP."
                )

            if production and smtp_debug is True:
                warnings.append(
                    "SMTP_DEBUG is enabled in production. Raw SMTP protocol debugging remains disabled "
                    "by the sender to prevent credential leakage."
                )
    elif production:
        warnings.append(
            "SMTP sender credentials are not configured. Email/OTP/password-recovery features that "
            "require outbound email will not be production-ready."
        )

    live_key_id = env_text("RAZORPAY_LIVE_KEY_ID")
    live_key_secret = env_text("RAZORPAY_LIVE_KEY_SECRET")
    if bool(live_key_id) != bool(live_key_secret):
        errors.append("Razorpay LIVE credentials are partial; set both RAZORPAY_LIVE_KEY_ID and RAZORPAY_LIVE_KEY_SECRET or neither.")
    elif production and not live_key_id:
        warnings.append("Razorpay LIVE credentials are absent. This is acceptable only while the online payment gateway remains disabled/test-only.")

    test_key_id = env_text("RAZORPAY_TEST_KEY_ID")
    test_key_secret = env_text("RAZORPAY_TEST_KEY_SECRET")
    if bool(test_key_id) != bool(test_key_secret):
        errors.append("Razorpay TEST credentials are partial; set both test values or neither.")

    seed_email = env_text("ADMIN_SEED_EMAIL") or env_text("ADMIN_EMAIL")
    seed_password = env_text("ADMIN_SEED_PASSWORD") or env_text("ADMIN_PASSWORD")
    if seed_email and (not seed_password or len(seed_password) < 10):
        errors.append("ADMIN_SEED_EMAIL is set but ADMIN_SEED_PASSWORD is missing or shorter than 10 characters.")
    if seed_password and not seed_email:
        warnings.append("ADMIN_SEED_PASSWORD is set without ADMIN_SEED_EMAIL; the explicit seed command will not create an admin.")

    timeout_raw = env_text("MONGO_SERVER_SELECTION_TIMEOUT_MS")
    if timeout_raw:
        try:
            if int(timeout_raw) < 500:
                errors.append("MONGO_SERVER_SELECTION_TIMEOUT_MS must be at least 500 ms.")
        except ValueError:
            errors.append("MONGO_SERVER_SELECTION_TIMEOUT_MS must be an integer number of milliseconds.")

    shiprocket_password = env_text("SHIPROCKET_PASSWORD")
    shiprocket_webhook = env_text("SHIPROCKET_WEBHOOK_TOKEN")
    if shiprocket_password and looks_placeholder(shiprocket_password):
        errors.append("SHIPROCKET_PASSWORD appears to contain placeholder/example text.")
    if shiprocket_webhook and looks_placeholder(shiprocket_webhook):
        errors.append("SHIPROCKET_WEBHOOK_TOKEN appears to contain placeholder/example text.")
    if production and (shiprocket_password or shiprocket_webhook):
        notes.append("Shiprocket runtime secrets are environment-managed. After validation, run scripts/scrub_shiprocket_secrets.py --confirm to remove matching legacy Mongo copies.")
    elif production:
        notes.append("Shiprocket runtime secrets are not environment-managed. If Shiprocket is enabled, migrate SHIPROCKET_PASSWORD and SHIPROCKET_WEBHOOK_TOKEN before go-live.")

    return production, errors, warnings, notes


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate NE FRESH environment configuration.")
    parser.add_argument("--production", action="store_true", help="Apply production requirements even if APP_ENV is not yet set.")
    args = parser.parse_args()

    production, errors, warnings, notes = validate(force_production=args.production)
    mode = "production" if production else "development/non-production"
    print(f"NE FRESH configuration preflight: {mode}")

    for message in errors:
        print(f"[ERROR] {message}")
    for message in warnings:
        print(f"[WARN]  {message}")
    for message in notes:
        print(f"[INFO]  {message}")

    if errors:
        print(f"[FAIL] {len(errors)} configuration error(s), {len(warnings)} warning(s).")
        return 2

    print(f"[OK] Configuration preflight passed with {len(warnings)} warning(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
