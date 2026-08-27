import pytest

from services import email_sender


def _gmail_env(monkeypatch):
    values = {
        "SMTP_HOST": "smtp.gmail.com",
        "SMTP_PORT": "587",
        "SMTP_USER": "otp@example.com",
        "SMTP_PASSWORD": "app-password-value",
        "SMTP_FROM": "otp@example.com",
        "SMTP_FROM_NAME": "NE FRESH",
        "SMTP_USE_TLS": "true",
        "SMTP_USE_SSL": "false",
        "SMTP_DEBUG": "false",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=30, **kwargs):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.ehlo_count = 0
        self.starttls_called = False
        self.login_args = None
        self.sendmail_args = None
        self.quit_called = False
        self.__class__.instances.append(self)

    def ehlo(self):
        self.ehlo_count += 1

    def starttls(self, context=None):
        self.starttls_called = True

    def login(self, user, password):
        self.login_args = (user, password)

    def sendmail(self, from_addr, recipients, message):
        self.sendmail_args = (from_addr, recipients, message)
        return {}

    def quit(self):
        self.quit_called = True


def test_gmail_587_starttls_success(monkeypatch):
    _gmail_env(monkeypatch)
    FakeSMTP.instances.clear()
    monkeypatch.setattr(email_sender.smtplib, "SMTP", FakeSMTP)

    result = email_sender.send_email(
        "customer@example.com",
        "OTP test",
        "<p>Hello</p>",
    )

    assert result["ok"] is True
    assert result["smtp_host"] == "smtp.gmail.com"
    assert result["smtp_port"] == 587
    assert result["smtp_tls"] is True
    assert result["smtp_ssl"] is False

    smtp = FakeSMTP.instances[-1]
    assert smtp.starttls_called is True
    assert smtp.login_args == ("otp@example.com", "app-password-value")
    assert smtp.sendmail_args[0] == "otp@example.com"
    assert smtp.sendmail_args[1] == ["customer@example.com"]
    assert smtp.quit_called is True


def test_gmail_587_rejects_wrong_tls_mode(monkeypatch):
    _gmail_env(monkeypatch)
    monkeypatch.setenv("SMTP_USE_TLS", "false")

    with pytest.raises(RuntimeError, match="port 587 requires"):
        email_sender.send_email(
            "customer@example.com",
            "OTP test",
            "<p>Hello</p>",
        )


def test_smtp_rejection_raises(monkeypatch):
    _gmail_env(monkeypatch)

    class RejectingSMTP(FakeSMTP):
        def sendmail(self, from_addr, recipients, message):
            return {recipients[0]: (550, b"rejected")}

    monkeypatch.setattr(email_sender.smtplib, "SMTP", RejectingSMTP)

    with pytest.raises(RuntimeError, match="SMTP rejected recipient"):
        email_sender.send_email(
            "customer@example.com",
            "OTP test",
            "<p>Hello</p>",
        )
