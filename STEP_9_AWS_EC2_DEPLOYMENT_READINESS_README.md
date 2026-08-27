# NE FRESH — Step 9 AWS EC2 Production Deployment Readiness

## Status

Step 9 packages the current Flask application for a controlled single-EC2 production deployment without changing the frozen route/form contracts or protected finance/order/inventory behavior.

Target topology:

```text
Internet
  -> DNS / Elastic IP
  -> Nginx :80/:443
  -> Gunicorn 127.0.0.1:8000 (systemd)
  -> Flask wsgi:app
  -> MongoDB / Atlas + SMTP + Razorpay + Shiprocket
  -> /srv/nefresh/shared/uploads (persistent EBS path)
```

## Step 9 changes

### Production process layer

- `requirements-prod.txt` extends the application requirements with Gunicorn.
- `deploy/gunicorn.conf.py` binds only to loopback, defaults to 2 sync workers, disables preload, recycles workers with max-request jitter, and writes access/error output to stdout/stderr for journald.
- `deploy/systemd/nefresh.service` runs as the non-root `nefresh` user, loads `/etc/nefresh/nefresh.env`, restarts failed workers, and makes the source tree read-only except for `/srv/nefresh/shared`.
- `wsgi:app` from Step 8 remains the production application target.

### Nginx / HTTPS readiness

- `deploy/nginx/nefresh.conf` proxies to `127.0.0.1:8000` and forwards Host, real IP, scheme, host and port headers.
- Static assets are served by Nginx from `/srv/nefresh/current/static/` with a deliberately short cache lifetime to avoid stale CSS/JS after UI releases.
- Uploads are **not** exposed through a new Nginx alias; they continue through the existing Flask route so current path validation and behavior remain unchanged.
- `deploy/install_system.sh` installs the Nginx/systemd files. After DNS points to the EC2 Elastic IP, use Certbot `--nginx --redirect` to create HTTPS safely.

### Trusted reverse proxy handling

`security.py` now applies Werkzeug `ProxyFix` only when `TRUST_PROXY_HEADERS=true`. Production uses exactly one trusted Nginx hop by default:

```text
PROXY_FIX_X_FOR=1
PROXY_FIX_X_PROTO=1
PROXY_FIX_X_HOST=1
PROXY_FIX_X_PORT=1
PROXY_FIX_X_PREFIX=0
```

This is disabled by default for local/direct execution so arbitrary client `X-Forwarded-*` headers are not trusted.

### Persistent runtime storage

Production uses:

```text
/srv/nefresh/shared/uploads
```

This is outside versioned release directories, so uploaded product/store/complaint media survives deployment and rollback. S3 remains a future scaling option; Step 9 does not change existing media URLs or storage logic.

### Shiprocket secret migration

Runtime Shiprocket API password and webhook token can now be supplied from environment variables:

```text
SHIPROCKET_EMAIL=
SHIPROCKET_PASSWORD=
SHIPROCKET_WEBHOOK_TOKEN=
```

Environment values take priority over legacy Mongo-backed values. When environment secret ownership is active, Admin settings no longer need to persist a new runtime password; saving settings clears the legacy password field. The webhook endpoint also prefers the environment token.

After configuring and validating the production environment, remove legacy Mongo copies with:

```bash
/srv/nefresh/current/.venv/bin/python \
  /srv/nefresh/current/scripts/run_with_env.py /etc/nefresh/nefresh.env -- \
  /srv/nefresh/current/.venv/bin/python \
  /srv/nefresh/current/scripts/scrub_shiprocket_secrets.py --confirm
```

The scrub command only removes fields whose corresponding environment secret is present.

## EC2 provisioning plan

### 1. EC2

Use a supported Ubuntu LTS instance. Start conservatively with enough memory for 2 Gunicorn workers; tune only after staging observation. Allocate an Elastic IP before DNS cutover.

Recommended Security Group:

- TCP 80 from the internet.
- TCP 443 from the internet.
- TCP 22 only from trusted administrator IPs during bootstrap, or prefer AWS Systems Manager Session Manager and close 22 afterward.
- Do **not** expose Gunicorn port 8000 publicly.
- Do **not** expose MongoDB from this EC2 Security Group. Atlas/database access should use its own network controls.

Recommended IAM role:

- `AmazonSSMManagedInstanceCore` for Session Manager.
- Least-privilege CloudWatch permissions if logs/metrics are shipped to CloudWatch.
- Least-privilege S3 permissions only if/when upload storage is migrated to S3.

Do not store AWS access keys in the application environment file.

### 2. Provision the host

From the release source:

```bash
sudo bash deploy/provision_ubuntu.sh
```

This installs Python tooling, Nginx, Git, rsync, curl and Certbot, creates the non-root `nefresh` service user, and creates:

```text
/srv/nefresh/releases
/srv/nefresh/shared/uploads
/etc/nefresh
```

### 3. Create the production environment

```bash
sudo cp deploy/env.production.example /etc/nefresh/nefresh.env
sudo chmod 600 /etc/nefresh/nefresh.env
sudo nano /etc/nefresh/nefresh.env
```

Generate a strong application secret, for example:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

Never commit the real environment file.

At minimum, production must have:

- `APP_ENV=production`
- a strong `APP_SECRET_KEY`
- `SESSION_COOKIE_SECURE=true`
- `TRUST_PROXY_HEADERS=true`
- exact HTTPS `CORS_ORIGINS`
- `MONGO_URI`
- absolute persistent `UPLOAD_FOLDER`
- loopback `GUNICORN_BIND`

Configure SMTP, Razorpay LIVE and Shiprocket environment values only when those live features are enabled.

### 4. Validate configuration before starting the app

After a release venv exists, the deploy script executes the same check automatically. It can also be run manually through the safe env loader:

```bash
.venv/bin/python scripts/run_with_env.py /etc/nefresh/nefresh.env -- \
  .venv/bin/python scripts/validate_config.py --production
```

No secret values are printed.

### 5. Install Nginx and systemd definitions

```bash
sudo DOMAIN=example.com WWW_DOMAIN=www.example.com bash deploy/install_system.sh
```

If there is no `www` hostname, omit `WWW_DOMAIN`.

### 6. DNS and HTTPS

Point the domain A record to the EC2 Elastic IP. After DNS resolves and Nginx is reachable on port 80:

```bash
sudo certbot --nginx -d example.com -d www.example.com --redirect
sudo certbot renew --dry-run
```

Do not run production user sessions over HTTP because production cookies are Secure.

## Release layout

Every release has its own source and virtual environment:

```text
/srv/nefresh/
  releases/
    20260827.... /
      .venv/
      app.py
      wsgi.py
      ...
  current -> releases/<active>
  previous -> releases/<previous>
  shared/
    uploads/
```

This makes both application code **and Python dependencies** rollback together.

## Safe deploy

From an approved source checkout/unpacked project:

```bash
sudo SOURCE_DIR=/path/to/approved/NE-FRESH-main \
  RUN_TESTS=1 \
  bash deploy/deploy.sh
```

`RUN_TESTS=1` is strongly recommended for staging and controlled production releases. The deploy script performs:

1. Create a new release directory.
2. Copy source without `.git`, local venvs, `.env` or local uploads.
3. Create a release-specific `.venv`.
4. Install `requirements-prod.txt`.
5. Run production configuration preflight.
6. Compile critical Python sources.
7. Optionally install dev test dependencies and run the full pytest suite.
8. Run an optional backup hook if configured.
9. Run explicit `scripts/init_db.py` exactly once.
10. Boot the new release on a temporary localhost port.
11. Require `/health/ready` to pass before switching.
12. Record the old release as `previous`.
13. Atomically switch `/srv/nefresh/current`.
14. Restart systemd.
15. Require post-switch health checks.
16. Automatically restore the prior release if the new app fails health checks.
17. Retain a bounded number of historical releases.

### Backup hook

The deployment package deliberately does not assume whether production MongoDB is Atlas-managed or self-hosted. Use a tested backup executable and configure:

```text
BACKUP_HOOK=/usr/local/sbin/nefresh-mongo-backup
REQUIRE_BACKUP_HOOK=1
```

The hook executes before database initialization. For production go-live, `REQUIRE_BACKUP_HOOK=1` is recommended after the backup procedure has been tested.

## Health and smoke checks

Direct Gunicorn health check:

```bash
sudo /srv/nefresh/current/deploy/healthcheck.sh
```

Public Nginx/HTTPS smoke check:

```bash
/srv/nefresh/current/.venv/bin/python scripts/smoke_test.py \
  --base-url https://example.com
```

Health semantics:

- `/health/live`: Flask worker is answering.
- `/health/ready`: MongoDB responds and persistent upload storage is writable.

## Rollback

```bash
sudo bash /srv/nefresh/current/deploy/rollback.sh
```

Rollback atomically switches `current` to `previous`, restarts the service and requires health to pass. The release-specific virtual environment rolls back with the source.

Important: if a future deployment introduces destructive database migrations, application rollback alone may not be sufficient; database restore/migration compatibility must be part of that release plan.

## Logging and observation

Gunicorn writes access/error output to stdout/stderr and systemd captures it in journald:

```bash
sudo systemctl status nefresh
sudo journalctl -u nefresh -f
sudo journalctl -u nefresh --since "30 minutes ago"
```

Nginx logs remain under `/var/log/nginx/` by default.

For AWS production, forward journald/Nginx metrics to CloudWatch using the EC2 IAM role rather than application AWS keys.

## Step 9 regression gate

Step 9 adds deployment package checks and ProxyFix runtime tests. On the normal Windows development environment run:

```powershell
python -m pytest
```

Expected gate:

```text
129 passed
0 failed
```

Warnings from `datetime.utcnow()` and the current Razorpay package remain tracked deprecations; they are not silently changed in this deployment step.

## What Step 9 intentionally does not do

- It does not deploy to a real EC2 instance yet; that is Step 10 staging rehearsal.
- It does not change any route URL, endpoint name, form contract or Mongo collection schema.
- It does not rewrite payment/refund/payout/order/inventory formulas.
- It does not move uploads to S3 yet.
- It does not hide deployment failures; config, boot, readiness and post-switch checks all fail closed.
