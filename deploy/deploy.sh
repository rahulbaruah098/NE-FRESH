#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Run as root: sudo SOURCE_DIR=/path/to/approved/source bash deploy/deploy.sh" >&2
  exit 2
fi

APP_ROOT="${APP_ROOT:-/srv/nefresh}"
APP_USER="${APP_USER:-nefresh}"
ENV_FILE="${ENV_FILE:-/etc/nefresh/nefresh.env}"
SERVICE="${SERVICE_NAME:-nefresh}"
SOURCE_DIR="${SOURCE_DIR:-$(pwd)}"
RUN_TESTS="${RUN_TESTS:-0}"
PREFLIGHT_PORT="${PREFLIGHT_PORT:-18001}"
KEEP_RELEASES="${KEEP_RELEASES:-5}"

if [[ ! -f "$SOURCE_DIR/wsgi.py" || ! -f "$SOURCE_DIR/requirements-prod.txt" ]]; then
  echo "SOURCE_DIR is not an approved NE FRESH release source: $SOURCE_DIR" >&2
  exit 2
fi
if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing production environment file: $ENV_FILE" >&2
  exit 2
fi

mkdir -p "$APP_ROOT/releases" "$APP_ROOT/shared/uploads"
chown -R "$APP_USER":www-data "$APP_ROOT/shared"
chmod 2770 "$APP_ROOT/shared" "$APP_ROOT/shared/uploads"

RELEASE_ID="${RELEASE_ID:-$(date -u +%Y%m%d%H%M%S)}"
RELEASE_DIR="$APP_ROOT/releases/$RELEASE_ID"
if [[ -e "$RELEASE_DIR" ]]; then
  echo "Release already exists: $RELEASE_DIR" >&2
  exit 2
fi
mkdir -p "$RELEASE_DIR"

rsync -a --delete \
  --exclude '.git/' \
  --exclude '.venv/' \
  --exclude 'venv/' \
  --exclude '__pycache__/' \
  --exclude '.pytest_cache/' \
  --exclude '.env' \
  --exclude 'uploads/' \
  "$SOURCE_DIR/" "$RELEASE_DIR/"

# ZIPs extracted on Windows may lose Unix executable bits. Normalize them in
# the release before any script is invoked directly by systemd/deploy tooling.
chmod 0755 "$RELEASE_DIR"/deploy/*.sh "$RELEASE_DIR"/scripts/*.py

python3 -m venv "$RELEASE_DIR/.venv"
"$RELEASE_DIR/.venv/bin/python" -m pip install --upgrade pip setuptools wheel
"$RELEASE_DIR/.venv/bin/python" -m pip install -r "$RELEASE_DIR/requirements-prod.txt"

# Use the same dotenv-compatible environment file as systemd without shell-
# sourcing secret values. This avoids interpretation of password characters.
ENV_RUN=("$RELEASE_DIR/.venv/bin/python" "$RELEASE_DIR/scripts/run_with_env.py" "$ENV_FILE" --)

cd "$RELEASE_DIR"
"${ENV_RUN[@]}" ".venv/bin/python" scripts/validate_config.py --production
".venv/bin/python" -m compileall -q app.py app_factory.py app_core.py config.py security.py uploads.py wsgi.py routes services helpers scripts

if [[ "$RUN_TESTS" == "1" ]]; then
  ".venv/bin/python" -m pip install -r requirements-dev.txt
  ".venv/bin/python" -m pytest
else
  echo "[INFO] Full pytest skipped during release install (RUN_TESTS=$RUN_TESTS). Run with RUN_TESTS=1 in staging/controlled production deploys."
fi

# Optional backup hook. Production operators should point this at a tested Atlas
# snapshot/mongodump workflow. It runs before index/seed initialization.
BACKUP_HOOK="${BACKUP_HOOK:-}"
REQUIRE_BACKUP_HOOK="${REQUIRE_BACKUP_HOOK:-0}"
if [[ -n "$BACKUP_HOOK" ]]; then
  if [[ ! -x "$BACKUP_HOOK" ]]; then
    echo "Configured BACKUP_HOOK is not executable: $BACKUP_HOOK" >&2
    exit 2
  fi
  "${ENV_RUN[@]}" "$BACKUP_HOOK"
elif [[ "$REQUIRE_BACKUP_HOOK" == "1" ]]; then
  echo "REQUIRE_BACKUP_HOOK=1 but BACKUP_HOOK is not configured." >&2
  exit 2
else
  echo "[WARN] No deployment backup hook configured. Confirm a current MongoDB/Atlas backup exists before production deployment."
fi

# Database initialization is explicit and happens once before workers are restarted.
"${ENV_RUN[@]}" ".venv/bin/python" scripts/init_db.py

# Boot the new release on a temporary loopback port before switching current.
"${ENV_RUN[@]}" /usr/bin/env GUNICORN_BIND="127.0.0.1:${PREFLIGHT_PORT}" ".venv/bin/gunicorn" --config deploy/gunicorn.conf.py --workers 1 wsgi:app >"/tmp/nefresh-preflight-${RELEASE_ID}.log" 2>&1 &
PREFLIGHT_PID=$!
cleanup_preflight() {
  if kill -0 "$PREFLIGHT_PID" >/dev/null 2>&1; then
    kill "$PREFLIGHT_PID" >/dev/null 2>&1 || true
    wait "$PREFLIGHT_PID" 2>/dev/null || true
  fi
}
trap cleanup_preflight EXIT

READY=0
for _ in $(seq 1 30); do
  if curl --fail --silent --max-time 3 "http://127.0.0.1:${PREFLIGHT_PORT}/health/ready" >/dev/null 2>&1; then
    READY=1
    break
  fi
  sleep 1
done
if [[ "$READY" != "1" ]]; then
  echo "New release failed preflight readiness. See /tmp/nefresh-preflight-${RELEASE_ID}.log" >&2
  exit 1
fi
cleanup_preflight
trap - EXIT

CURRENT="$APP_ROOT/current"
PREVIOUS="$APP_ROOT/previous"
OLD_TARGET=""
if [[ -L "$CURRENT" ]]; then
  OLD_TARGET="$(readlink -f "$CURRENT")"
  [[ -d "$OLD_TARGET" ]] && ln -sfn "$OLD_TARGET" "$PREVIOUS"
fi

ln -sfn "$RELEASE_DIR" "$CURRENT.next"
mv -Tf "$CURRENT.next" "$CURRENT"
chown -h "$APP_USER":www-data "$CURRENT" || true

systemctl restart "$SERVICE"
if ! "$CURRENT/deploy/healthcheck.sh"; then
  echo "New release failed post-switch health check; attempting automatic rollback." >&2
  if [[ -n "$OLD_TARGET" && -d "$OLD_TARGET" ]]; then
    ln -sfn "$OLD_TARGET" "$CURRENT.next"
    mv -Tf "$CURRENT.next" "$CURRENT"
    systemctl restart "$SERVICE"
    "$CURRENT/deploy/healthcheck.sh" || true
  fi
  exit 1
fi

# Keep a small release history; never delete current/previous targets.
mapfile -t RELEASES < <(find "$APP_ROOT/releases" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' | sort -nr | awk '{print $2}')
for RELEASE in "${RELEASES[@]:$KEEP_RELEASES}"; do
  if [[ "$RELEASE" != "$(readlink -f "$CURRENT" 2>/dev/null || true)" && "$RELEASE" != "$(readlink -f "$PREVIOUS" 2>/dev/null || true)" ]]; then
    rm -rf "$RELEASE"
  fi
done

echo "Deployment complete: $RELEASE_ID"
