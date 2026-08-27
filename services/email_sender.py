"""Provider-neutral SMTP email transport for NE FRESH.

The current production provider is Gmail / Google Workspace SMTP using an App
Password.  Business logic (OTP generation, expiry, verification, resend rules,
newsletter capture, etc.) must remain outside this module so the transport can
be changed later without rewriting those flows.
"""
from __future__ import annotations

import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid

from config import _env_bool


def _mask_value(value: str) -> str:
    """Return a log-safe masked representation of an email/value."""
    value = str(value or "")
    if not value:
        return ""

    if "@" in value:
        left, right = value.split("@", 1)
        if len(left) <= 2:
            left_masked = left[:1] + "***"
        else:
            left_masked = left[:2] + "***" + left[-1:]
        return f"{left_masked}@{right}"

    if len(value) <= 6:
        return value[:2] + "***"
    return value[:4] + "***" + value[-3:]


def _smtp_settings() -> dict:
    """Read and validate the SMTP transport settings from the environment."""
    smtp_host = (os.getenv("SMTP_HOST") or "").strip()

    try:
        smtp_port = int((os.getenv("SMTP_PORT") or "587").strip())
    except (TypeError, ValueError) as exc:
        raise RuntimeError("SMTP_PORT must be a valid number.") from exc

    if smtp_port < 1 or smtp_port > 65535:
        raise RuntimeError("SMTP_PORT must be between 1 and 65535.")

    smtp_user = (os.getenv("SMTP_USER") or "").strip()
    smtp_password = (os.getenv("SMTP_PASSWORD") or "").strip()
    smtp_from = (os.getenv("SMTP_FROM") or "").strip()
    smtp_from_name = (os.getenv("SMTP_FROM_NAME") or "NE FRESH").strip() or "NE FRESH"

    smtp_use_ssl = _env_bool("SMTP_USE_SSL", smtp_port == 465)
    smtp_use_tls = _env_bool("SMTP_USE_TLS", not smtp_use_ssl)
    smtp_debug = _env_bool("SMTP_DEBUG", False)

    if not smtp_host:
        raise RuntimeError("SMTP_HOST is missing in the environment.")
    if not smtp_user:
        raise RuntimeError("SMTP_USER is missing in the environment.")
    if not smtp_password:
        raise RuntimeError("SMTP_PASSWORD is missing in the environment.")
    if not smtp_from:
        raise RuntimeError("SMTP_FROM is missing in the environment.")
    if "@" not in smtp_user:
        raise RuntimeError("SMTP_USER must be a valid email address.")
    if "@" not in smtp_from:
        raise RuntimeError("SMTP_FROM must be a valid sender email address.")
    if smtp_use_ssl and smtp_use_tls:
        raise RuntimeError("SMTP_USE_SSL and SMTP_USE_TLS cannot both be enabled.")

    # Gmail's supported application SMTP modes are STARTTLS/587 or SSL/465.
    if smtp_host.lower() == "smtp.gmail.com":
        if smtp_port == 587 and (not smtp_use_tls or smtp_use_ssl):
            raise RuntimeError(
                "Gmail SMTP port 587 requires SMTP_USE_TLS=true and SMTP_USE_SSL=false."
            )
        if smtp_port == 465 and (not smtp_use_ssl or smtp_use_tls):
            raise RuntimeError(
                "Gmail SMTP port 465 requires SMTP_USE_SSL=true and SMTP_USE_TLS=false."
            )
        if smtp_port not in {465, 587}:
            raise RuntimeError("Gmail SMTP must use port 587 (STARTTLS) or 465 (SSL).")

    return {
        "host": smtp_host,
        "port": smtp_port,
        "user": smtp_user,
        "password": smtp_password,
        "from": smtp_from,
        "from_name": smtp_from_name,
        "use_ssl": smtp_use_ssl,
        "use_tls": smtp_use_tls,
        "debug": smtp_debug,
    }


def send_email(to_email, subject, body):
    """Send one HTML email and return only after SMTP accepts the recipient.

    This function deliberately does not start background threads.  A caller may
    report success only after this function returns successfully.

    The SMTP password is never logged.  Raw smtplib protocol debugging is also
    intentionally disabled because AUTH traces can expose reversible credential
    material even when SMTP_DEBUG is set.
    """
    settings = _smtp_settings()

    to_email = (to_email or "").strip()
    subject = (subject or "").strip()
    body = body or ""

    if not to_email:
        raise RuntimeError("Recipient email is missing.")
    if "@" not in to_email:
        raise RuntimeError("Recipient email is invalid.")

    msg = MIMEMultipart("alternative")
    msg["From"] = formataddr((settings["from_name"], settings["from"]))
    msg["To"] = to_email
    msg["Subject"] = subject
    msg["Reply-To"] = settings["from"]
    msg["Date"] = formatdate(localtime=True)

    try:
        sender_domain = settings["from"].split("@", 1)[1]
        msg["Message-ID"] = make_msgid(domain=sender_domain)
    except Exception:
        msg["Message-ID"] = make_msgid()

    msg.attach(MIMEText(body, "html", "utf-8"))

    print(
        "[EMAIL SMTP START] "
        f"host={settings['host']} port={settings['port']} "
        f"ssl={settings['use_ssl']} tls={settings['use_tls']} "
        f"user={_mask_value(settings['user'])} "
        f"from={_mask_value(settings['from'])} to={_mask_value(to_email)}"
    )

    if settings["debug"]:
        print(
            "[EMAIL SMTP DEBUG] Raw SMTP protocol debug is disabled to prevent "
            "credential leakage; sanitized transport diagnostics remain enabled."
        )

    server = None
    tls_context = ssl.create_default_context()

    try:
        if settings["use_ssl"]:
            server = smtplib.SMTP_SSL(
                settings["host"],
                settings["port"],
                timeout=30,
                context=tls_context,
            )
            server.ehlo()
        else:
            server = smtplib.SMTP(settings["host"], settings["port"], timeout=30)
            server.ehlo()

            if settings["use_tls"]:
                server.starttls(context=tls_context)
                server.ehlo()

        server.login(settings["user"], settings["password"])

        failed_recipients = server.sendmail(
            settings["from"],
            [to_email],
            msg.as_string(),
        )

        if failed_recipients:
            rejected = ", ".join(_mask_value(item) for item in failed_recipients.keys())
            raise RuntimeError(f"SMTP rejected recipient(s): {rejected}")

        print(f"[EMAIL SMTP ACCEPTED] to={_mask_value(to_email)}")

        # Keep the legacy result shape for callers that already inspect it.
        return {
            "ok": True,
            "to": to_email,
            "subject": subject,
            "smtp_host": settings["host"],
            "smtp_port": settings["port"],
            "smtp_ssl": settings["use_ssl"],
            "smtp_tls": settings["use_tls"],
            "error": "",
        }

    except Exception as exc:
        print(
            f"[EMAIL SMTP ERROR] to={_mask_value(to_email)} "
            f"error={type(exc).__name__}: {exc}"
        )
        raise

    finally:
        try:
            if server:
                server.quit()
        except Exception:
            pass
