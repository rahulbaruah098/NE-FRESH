#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Run as root: sudo DOMAIN=yourdomain.com bash deploy/install_system.sh" >&2
  exit 2
fi

DOMAIN="${DOMAIN:-}"
WWW_DOMAIN="${WWW_DOMAIN:-}"
if [[ -z "$DOMAIN" ]]; then
  echo "DOMAIN is required, e.g. DOMAIN=example.com" >&2
  exit 2
fi
SERVER_NAMES="$DOMAIN"
if [[ -n "$WWW_DOMAIN" ]]; then
  SERVER_NAMES="$SERVER_NAMES $WWW_DOMAIN"
fi

SOURCE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-/etc/nefresh/nefresh.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE. Create it from deploy/env.production.example first." >&2
  exit 2
fi
chmod 600 "$ENV_FILE"

install -m 0644 "$SOURCE_ROOT/deploy/systemd/nefresh.service" /etc/systemd/system/nefresh.service
sed "s/__NEFRESH_DOMAIN__/$SERVER_NAMES/g" "$SOURCE_ROOT/deploy/nginx/nefresh.conf" > /etc/nginx/sites-available/nefresh
ln -sfn /etc/nginx/sites-available/nefresh /etc/nginx/sites-enabled/nefresh
rm -f /etc/nginx/sites-enabled/default

systemctl daemon-reload
systemctl enable nefresh
nginx -t
systemctl reload nginx

echo "System files installed."
echo "After the first release is deployed and DNS resolves, obtain HTTPS with:"
if [[ -n "$WWW_DOMAIN" ]]; then
  echo "  sudo certbot --nginx -d $DOMAIN -d $WWW_DOMAIN --redirect"
else
  echo "  sudo certbot --nginx -d $DOMAIN --redirect"
fi
