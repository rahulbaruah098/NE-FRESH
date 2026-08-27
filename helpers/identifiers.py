"""Identifier compatibility helpers for legacy/new MongoDB ID shapes."""

from bson import ObjectId

def _store_identity_values(store_id):
    """Return ObjectId/string store ids so legacy and new rows both match.

    Some old records were saved with store_id as a string, while current store
    records use ObjectId. Dashboard/report queries must read both shapes to keep
    GMV, products and order counts accurate after migrations/imports.
    """
    values = []

    def add(value):
        if value is None:
            return
        if value not in values:
            values.append(value)

    add(store_id)
    add(str(store_id))

    try:
        add(ObjectId(str(store_id)))
    except Exception:
        pass

    return values

def _order_identity_values(order_ids):
    """Return ObjectId/string order ids for transaction joins."""
    values = []

    def add(value):
        if value is None:
            return
        if value not in values:
            values.append(value)

    for oid in order_ids or []:
        add(oid)
        add(str(oid))
        try:
            add(ObjectId(str(oid)))
        except Exception:
            pass

    return values
