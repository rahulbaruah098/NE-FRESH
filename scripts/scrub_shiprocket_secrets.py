#!/usr/bin/env python3
"""Remove legacy Shiprocket plaintext secrets from Mongo after env migration.

Runtime code prefers SHIPROCKET_PASSWORD and SHIPROCKET_WEBHOOK_TOKEN from the
process environment. This command only clears a Mongo field when the matching
environment secret is present, so it cannot accidentally disable the provider.
"""
from __future__ import annotations

import argparse
import os

from extensions import mongo

EXTERNAL_DELIVERY_SETTINGS_KEY = "external_delivery_settings"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm", action="store_true", help="Actually remove eligible Mongo secret fields.")
    args = parser.parse_args()

    unset = {}
    if (os.getenv("SHIPROCKET_PASSWORD") or "").strip():
        unset["shiprocket_password"] = ""
    if (os.getenv("SHIPROCKET_WEBHOOK_TOKEN") or "").strip():
        unset["shiprocket_webhook_token"] = ""

    if not unset:
        print("No environment-managed Shiprocket secrets are present; nothing to scrub.")
        return 0

    print("Eligible Mongo secret fields: " + ", ".join(sorted(unset)))
    if not args.confirm:
        print("Dry run only. Re-run with --confirm after validating the production environment.")
        return 0

    result = mongo.platform_settings.update_one(
        {"key": EXTERNAL_DELIVERY_SETTINGS_KEY},
        {"$unset": unset},
    )
    print(f"Scrub complete. matched={result.matched_count} modified={result.modified_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
