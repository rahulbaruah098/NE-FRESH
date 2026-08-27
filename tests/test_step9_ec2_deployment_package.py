from __future__ import annotations

from pathlib import Path

import pytest

from tests.support.source_contracts import project_root

ROOT = project_root()


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


@pytest.mark.static
def test_step9_production_requirements_add_gunicorn_without_replacing_app_requirements():
    text = _text("requirements-prod.txt")
    assert "-r requirements.txt" in text
    assert "gunicorn==" in text


@pytest.mark.static
def test_step9_gunicorn_is_loopback_bound_and_not_preloaded():
    text = _text("deploy/gunicorn.conf.py")
    assert '127.0.0.1:8000' in text
    assert "preload_app = False" in text
    assert 'worker_class = "sync"' in text
    assert "max_requests" in text
    assert "forwarded_allow_ips" in text


@pytest.mark.static
def test_step9_systemd_uses_release_symlink_external_env_and_journal():
    text = _text("deploy/systemd/nefresh.service")
    assert "User=nefresh" in text
    assert "WorkingDirectory=/srv/nefresh/current" in text
    assert "EnvironmentFile=/etc/nefresh/nefresh.env" in text
    assert "wsgi:app" in text
    assert "ReadWritePaths=/srv/nefresh/shared" in text
    assert "StandardOutput=journal" in text
    assert "Restart=on-failure" in text


@pytest.mark.static
def test_step9_nginx_is_only_public_proxy_and_preserves_forwarded_headers():
    text = _text("deploy/nginx/nefresh.conf")
    assert "server 127.0.0.1:8000" in text
    assert "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for" in text
    assert "proxy_set_header X-Forwarded-Proto $scheme" in text
    assert "proxy_set_header X-Forwarded-Host $host" in text
    assert "proxy_set_header X-Forwarded-Port $server_port" in text
    assert "alias /srv/nefresh/current/static/" in text
    assert "location /uploads/" not in text


@pytest.mark.static
def test_step9_production_env_contract_covers_proxy_uploads_workers_and_secret_overrides():
    text = _text("deploy/env.production.example")
    for required in (
        "APP_ENV=production",
        "SESSION_COOKIE_SECURE=true",
        "TRUST_PROXY_HEADERS=true",
        "PROXY_FIX_X_FOR=1",
        "UPLOAD_FOLDER=/srv/nefresh/shared/uploads",
        "GUNICORN_BIND=127.0.0.1:8000",
        "SHIPROCKET_PASSWORD=",
        "SHIPROCKET_WEBHOOK_TOKEN=",
    ):
        assert required in text
    assert "mongodb://localhost" not in text


@pytest.mark.static
def test_step9_deploy_script_has_preflight_explicit_db_init_atomic_switch_and_health_gate():
    text = _text("deploy/deploy.sh")
    for required in (
        "scripts/validate_config.py --production",
        "scripts/init_db.py",
        "PREFLIGHT_PORT",
        "health/ready",
        "mv -Tf",
        "systemctl restart",
        "healthcheck.sh",
        "RUN_TESTS",
        "automatic rollback",
    ):
        assert required in text


@pytest.mark.static
def test_step9_rollback_and_health_scripts_are_explicit():
    rollback = _text("deploy/rollback.sh")
    health = _text("deploy/healthcheck.sh")
    smoke = _text("scripts/smoke_test.py")
    assert "/srv/nefresh/previous" not in rollback  # assembled from APP_ROOT, not a second hard-coded tree
    assert 'PREVIOUS="$APP_ROOT/previous"' in rollback
    assert "systemctl restart" in rollback
    assert "healthcheck.sh" in rollback
    assert "/health/live" in health and "/health/ready" in health
    assert "/health/live" in smoke and "/health/ready" in smoke


@pytest.mark.static
def test_step9_provisioning_installs_nginx_certbot_and_non_root_service_user():
    provision = _text("deploy/provision_ubuntu.sh")
    install = _text("deploy/install_system.sh")
    assert "python3-venv" in provision
    assert "nginx" in provision
    assert "certbot" in provision
    assert "useradd --system" in provision
    assert "/srv/nefresh" in provision
    assert "shared/uploads" in provision
    assert "nginx -t" in install
    assert "certbot --nginx" in install


@pytest.mark.static
def test_step9_config_preflight_requires_supported_proxy_topology_and_private_gunicorn_bind():
    text = _text("scripts/validate_config.py")
    assert "TRUST_PROXY_HEADERS=true is required" in text
    assert "PROXY_FIX_X_FOR" in text
    assert "GUNICORN_BIND must remain loopback/unix-only" in text
    assert "scrub_shiprocket_secrets.py --confirm" in text


@pytest.mark.static
def test_step9_shiprocket_runtime_secrets_prefer_environment_and_stop_new_plaintext_storage():
    service = _text("services/delivery_integrations/shiprocket_service.py")
    route = _text("routes/external_delivery/routes.py")
    scrub = _text("scripts/scrub_shiprocket_secrets.py")
    assert 'os.getenv("SHIPROCKET_PASSWORD")' in service
    assert 'os.getenv("SHIPROCKET_EMAIL")' in service
    assert 'os.getenv("SHIPROCKET_WEBHOOK_TOKEN")' in route
    assert 'update_data["shiprocket_password"] = ""' in route
    assert '"$unset": unset' in scrub
