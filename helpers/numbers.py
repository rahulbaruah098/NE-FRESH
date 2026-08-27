"""Numeric normalization helpers extracted during Step 5.

These functions preserve the legacy conversion behavior used by delivery and
shipping workflows while keeping parsing concerns out of app_core.py.
"""

def _get_float_or_none(value):
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(value)
    except Exception:
        return None

def _delivery_int(value, default=0):
    try:
        if value is None or str(value).strip() == "":
            return int(default)

        if isinstance(value, bool):
            return 1 if value else 0

        value_str = str(value).strip().lower()

        if value_str in ["true", "yes", "on"]:
            return 1

        if value_str in ["false", "no", "off"]:
            return 0

        return int(value)
    except Exception:
        return int(default)

def _delivery_float_or_none(value):
    try:
        if value is None or str(value).strip() == "":
            return None

        return float(value)
    except Exception:
        return None

def _delivery_float_or_default(value, default=0.0, minimum=0.0):
    try:
        if value is None or str(value).strip() == "":
            value = default
        value = float(value)
    except Exception:
        value = float(default)

    try:
        minimum = float(minimum)
    except Exception:
        minimum = 0.0

    if value < minimum:
        value = minimum

    return round(value, 3)
