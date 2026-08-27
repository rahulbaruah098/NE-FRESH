#!/usr/bin/env python3
"""Explicit NE FRESH MongoDB initialization command.

Database mutations never run during application import. Run this command once
for initial setup and again only when a release intentionally adds idempotent
indexes or bootstrap requirements.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from database_init import initialize_database  # noqa: E402
from extensions import mongo  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Initialize NE FRESH MongoDB safely and explicitly.")
    parser.add_argument("--skip-indexes", action="store_true", help="Do not create/verify MongoDB indexes.")
    parser.add_argument("--skip-admin-seed", action="store_true", help="Do not run the optional ADMIN_SEED_* bootstrap logic.")
    parser.add_argument("--check-only", action="store_true", help="Only verify that MongoDB responds to ping; make no changes.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        mongo.command("ping")
    except Exception as exc:
        print(f"[FAIL] MongoDB is not reachable: {exc}", file=sys.stderr)
        return 2

    print("[OK] MongoDB connection")
    if args.check_only:
        print("[OK] Check-only mode; no database mutations performed.")
        return 0

    initialize_database(
        skip_indexes=args.skip_indexes,
        skip_admin_seed=args.skip_admin_seed,
    )
    if not args.skip_indexes:
        print("[OK] MongoDB indexes verified/created")
    if not args.skip_admin_seed:
        print("[OK] Optional admin seed step completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
