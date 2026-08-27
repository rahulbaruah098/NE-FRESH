#!/usr/bin/env python3
"""HTTP smoke test for a running NE FRESH deployment."""
from __future__ import annotations

import argparse
import sys

import requests


def check(session, base_url: str, path: str, expected=(200,)) -> None:
    response = session.get(base_url.rstrip("/") + path, timeout=10, allow_redirects=False)
    if response.status_code not in expected:
        raise RuntimeError(f"{path} returned HTTP {response.status_code}; expected {expected}")
    print(f"[OK] {path} -> {response.status_code}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test a running NE FRESH instance.")
    parser.add_argument("--base-url", default="http://127.0.0.1", help="Base URL through Nginx or directly to Gunicorn.")
    parser.add_argument("--host", default="", help="Optional Host header for local Nginx checks.")
    args = parser.parse_args()

    session = requests.Session()
    if args.host:
        session.headers["Host"] = args.host

    try:
        check(session, args.base_url, "/health/live")
        check(session, args.base_url, "/health/ready")
        check(session, args.base_url, "/", expected=(200, 301, 302))
    except Exception as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1

    print("NE FRESH smoke test passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
