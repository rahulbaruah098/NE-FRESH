"""Deterministic Flask application factory for NE FRESH.

Step 8 centralizes process startup here so both local development and Gunicorn
load exactly the same configured Flask application.  The project still uses
legacy ``@app.route`` decorators, so the factory is intentionally idempotent
within one Python process: the configured app is created once and the route
registries are imported once.

Database indexes/seeds are deliberately NOT run here.  They remain an explicit
operator action through ``scripts/init_db.py``.
"""
from __future__ import annotations

from threading import RLock
from typing import Optional

from flask import Flask

from config import configure_application, warn_missing_production_sender_settings
from logging_config import log_debug, log_warning
from security import register_security, register_trusted_proxy
from uploads import configure_uploads

# Preserve the historical Flask import name used when app_core.py owned app
# construction.  This keeps app.name/root/template/static discovery compatible.
_FLASK_IMPORT_NAME = "app_core"

_app: Optional[Flask] = None
_routes_registered = False
_factory_lock = RLock()


def _build_base_app() -> Flask:
    """Create/configure the Flask object without importing application routes."""
    flask_app = Flask(_FLASK_IMPORT_NAME)
    configure_application(flask_app, log_warning=log_warning)
    register_trusted_proxy(flask_app)
    register_security(flask_app)
    configure_uploads(flask_app)
    warn_missing_production_sender_settings(log_warning=log_warning)
    return flask_app


def get_base_app() -> Flask:
    """Return the process-local configured app, creating it once if necessary."""
    global _app
    if _app is None:
        with _factory_lock:
            if _app is None:
                _app = _build_base_app()
    return _app


def _register_application_routes() -> None:
    """Import the legacy route registries once so decorators attach to the app."""
    global _routes_registered
    if _routes_registered:
        return

    with _factory_lock:
        if _routes_registered:
            return

        # app_core binds shared legacy helpers to the same base app and installs
        # site-wide template context providers.  It no longer constructs Flask.
        import app_core  # noqa: F401

        # Central route-registration list.  Endpoint names/paths remain exactly
        # as frozen in the regression baseline; no Blueprint namespacing is used.
        import routes.public.routes  # noqa: F401
        import routes.location.routes  # noqa: F401
        import routes.auth.routes  # noqa: F401
        import routes.customer.routes  # noqa: F401
        import routes.products.routes  # noqa: F401
        import routes.cart.routes  # noqa: F401
        import routes.orders.routes  # noqa: F401
        import routes.admin.routes  # noqa: F401
        import routes.store.routes  # noqa: F401
        import routes.delivery.routes  # noqa: F401
        import routes.external_delivery.routes  # noqa: F401
        import routes.api.routes  # noqa: F401
        import routes.health.routes  # noqa: F401

        _routes_registered = True
        log_debug("\n=== ROUTES LOADED BY FACTORY ===")
        log_debug(get_base_app().url_map)
        log_debug("=================================\n")


def create_app() -> Flask:
    """Return the fully configured, route-registered NE FRESH application.

    This factory is safe to call repeatedly inside one worker.  Repeated calls
    return the same Flask object and never duplicate routes/security/context
    hooks.  Gunicorn workers each receive their own process-local instance.
    """
    flask_app = get_base_app()
    _register_application_routes()
    log_debug("[APPLICATION READY]", flask_app.name)
    return flask_app


__all__ = ["create_app", "get_base_app"]
