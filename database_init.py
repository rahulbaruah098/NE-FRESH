"""Explicit database bootstrap helpers.

Nothing in this module runs automatically during application import.
"""
from __future__ import annotations

import os
from datetime import datetime

from werkzeug.security import generate_password_hash

from extensions import mongo
from logging_config import log_warning
from mongo_db import ensure_mongo_indexes


def ensure_admin_seed_password():
    admin_email = (os.getenv("ADMIN_SEED_EMAIL") or os.getenv("ADMIN_EMAIL") or "").strip().lower()
    admin_password = (os.getenv("ADMIN_SEED_PASSWORD") or os.getenv("ADMIN_PASSWORD") or "").strip()
    admin_name = (os.getenv("ADMIN_SEED_NAME") or "Administrator").strip() or "Administrator"
    admin_phone = (os.getenv("ADMIN_SEED_PHONE") or "").strip()

    if not admin_email:
        return

    admin = mongo.users.find_one({"email": admin_email})
    if not admin:
        if not admin_password or len(admin_password) < 10:
            log_warning("[SECURITY WARNING] ADMIN_SEED_PASSWORD is missing/too short. Admin seed skipped.")
            return
        mongo.users.insert_one({
            "name": admin_name,
            "email": admin_email,
            "phone": admin_phone,
            "password_hash": generate_password_hash(admin_password),
            "role": "admin",
            "phone_verified": 1,
            "is_active": 1,
            "created_at": datetime.utcnow().isoformat(),
        })
        return

    if admin.get("password_hash") == "!!set_in_app!!":
        if not admin_password or len(admin_password) < 10:
            log_warning(
                "[SECURITY WARNING] Existing admin placeholder password found, "
                "but ADMIN_SEED_PASSWORD is missing/too short."
            )
            return
        mongo.users.update_one(
            {"_id": admin["_id"]},
            {"$set": {"password_hash": generate_password_hash(admin_password)}},
        )


def initialize_database(*, skip_indexes: bool = False, skip_admin_seed: bool = False) -> None:
    if not skip_indexes:
        ensure_mongo_indexes()
    if not skip_admin_seed:
        ensure_admin_seed_password()
