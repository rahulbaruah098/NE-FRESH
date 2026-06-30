"""Application entry point for the refactored NE FRESH Flask app.

The original single app.py has been split into route-wise folders.
All route decorators still register on the same Flask app from app_core.py,
so existing url_for(...) endpoint names, templates, sessions, MongoDB usage and logic remain unchanged.
"""

from app_core import app

# Import route modules so their @app.route decorators register on the same Flask app.
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

import os


if os.getenv("NEFRESH_DEBUG_LOGS", "0").strip().lower() in ["1", "true", "yes", "on"]:
    print("\n=== ROUTES LOADED FROM app.py ===")
    print(app.url_map)
    print("=================================\n")

if __name__ == "__main__":
    debug_enabled = os.getenv("FLASK_DEBUG", "0").strip().lower() in ["1", "true", "yes", "on"]

    app.config["DEBUG"] = debug_enabled
    app.config["TEMPLATES_AUTO_RELOAD"] = debug_enabled
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0 if debug_enabled else 31536000
    app.jinja_env.auto_reload = debug_enabled

    extra_files = [
        "app.py",
        "app_core.py",
    ]

    watch_dirs = [
        "routes",
        "templates",
        "static",
    ]

    for watch_dir in watch_dirs:
        if os.path.exists(watch_dir):
            for root, dirs, files in os.walk(watch_dir):
                dirs[:] = [
                    d for d in dirs
                    if d not in ["__pycache__", ".git", "venv", "env", "node_modules"]
                ]

                for file in files:
                    if file.endswith((".py", ".html", ".css", ".js")):
                        extra_files.append(os.path.join(root, file))

    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        debug=debug_enabled,
        use_reloader=debug_enabled,
        extra_files=extra_files if debug_enabled else None,
    )