#!/usr/bin/env bash
set -Eeuo pipefail

BASE_URL="${HEALTH_BASE_URL:-http://127.0.0.1:8000}"
HOST_HEADER="${HEALTH_HOST_HEADER:-}"
CURL_ARGS=(--fail --silent --show-error --max-time "${HEALTH_TIMEOUT:-10}")
if [[ -n "$HOST_HEADER" ]]; then
  CURL_ARGS+=(-H "Host: $HOST_HEADER")
fi

curl "${CURL_ARGS[@]}" "$BASE_URL/health/live" >/dev/null
curl "${CURL_ARGS[@]}" "$BASE_URL/health/ready" >/dev/null

echo "NE FRESH health check passed: $BASE_URL"
