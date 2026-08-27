#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Run as root: sudo bash deploy/provision_ubuntu.sh" >&2
  exit 2
fi

APP_USER="${APP_USER:-nefresh}"
APP_ROOT="${APP_ROOT:-/srv/nefresh}"
ENV_DIR="${ENV_DIR:-/etc/nefresh}"

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  python3 python3-venv python3-pip nginx git rsync curl ca-certificates \
  build-essential certbot python3-certbot-nginx

if ! id "$APP_USER" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "/var/lib/$APP_USER" --shell /usr/sbin/nologin "$APP_USER"
fi

mkdir -p "$APP_ROOT/releases" "$APP_ROOT/shared/uploads" "$ENV_DIR"
chown -R "$APP_USER":www-data "$APP_ROOT"
chmod 2770 "$APP_ROOT/shared" "$APP_ROOT/shared/uploads"
chmod 0750 "$APP_ROOT" "$APP_ROOT/releases"
chmod 0700 "$ENV_DIR"

echo "Provisioning complete."
echo "Next: copy deploy/env.production.example to $ENV_DIR/nefresh.env, fill real values, chmod 600, then run deploy/install_system.sh." 
