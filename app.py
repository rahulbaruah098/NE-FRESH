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


print("\n=== ROUTES LOADED FROM app.py ===")
print(app.url_map)
print("=================================\n")

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True,
        use_reloader=False
    )