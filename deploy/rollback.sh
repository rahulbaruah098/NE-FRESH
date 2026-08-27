#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Run as root: sudo bash deploy/rollback.sh" >&2
  exit 2
fi

APP_ROOT="${APP_ROOT:-/srv/nefresh}"
CURRENT="$APP_ROOT/current"
PREVIOUS="$APP_ROOT/previous"
SERVICE="${SERVICE_NAME:-nefresh}"

if [[ ! -L "$PREVIOUS" ]]; then
  echo "No previous release symlink exists at $PREVIOUS" >&2
  exit 2
fi

PREVIOUS_TARGET="$(readlink -f "$PREVIOUS")"
if [[ ! -d "$PREVIOUS_TARGET" ]]; then
  echo "Previous release target is missing: $PREVIOUS_TARGET" >&2
  exit 2
fi

CURRENT_TARGET=""
if [[ -L "$CURRENT" ]]; then
  CURRENT_TARGET="$(readlink -f "$CURRENT")"
fi

ln -sfn "$PREVIOUS_TARGET" "$CURRENT.next"
mv -Tf "$CURRENT.next" "$CURRENT"
if [[ -n "$CURRENT_TARGET" && -d "$CURRENT_TARGET" ]]; then
  ln -sfn "$CURRENT_TARGET" "$PREVIOUS"
fi

systemctl restart "$SERVICE"
"$CURRENT/deploy/healthcheck.sh"

echo "Rollback complete: $PREVIOUS_TARGET"
