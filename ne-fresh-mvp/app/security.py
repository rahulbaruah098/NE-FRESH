
from functools import wraps
from flask import abort, current_app
from flask_login import current_user

def role_required(*roles):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if not current_user.is_authenticated or current_user.role not in roles:
                return abort(403)
            return fn(*a, **kw)
        return wrapper
    return deco

def delivery_enabled_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not current_app.config.get("DELIVERY_ENABLED"):
            return abort(404)
        return fn(*a, **kw)
    return wrapper

def assert_pincode(pin: str, allowed: str):
    if pin != allowed:
        from werkzeug.exceptions import UnprocessableEntity
        raise UnprocessableEntity("Service available only in PIN 796009.")
