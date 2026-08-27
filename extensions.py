"""Shared external resources for NE FRESH.

Step 3 keeps the existing PyMongo client implementation in mongo_db.py for
compatibility while giving application code a stable extensions import path.
"""
from mongo_db import client as mongo_client, mongo


def ping_mongo():
    return mongo.command("ping")

__all__ = ["mongo", "mongo_client", "ping_mongo"]
