"""Store route registry.

Step 7 splits the previous giant route module into domain files without
changing Flask endpoint names, URL paths, methods or business behaviour.
``app.py`` continues importing this module, so the external bootstrap contract
is unchanged.
"""

# Load shared hooks/helpers first, then import each domain module so its
# existing @app.route decorators register on the same Flask application.
from . import shared as _shared  # noqa: F401

from . import public_storefront as _public_storefront_routes  # noqa: F401
from . import dashboard_settings as _dashboard_settings_routes  # noqa: F401
from . import products as _products_routes  # noqa: F401
from . import orders as _orders_routes  # noqa: F401
from . import delivery_management as _delivery_management_routes  # noqa: F401
from . import returns as _returns_routes  # noqa: F401
from . import inventory as _inventory_routes  # noqa: F401
from . import categories as _categories_routes  # noqa: F401
from . import reviews as _reviews_routes  # noqa: F401
from . import complaints as _complaints_routes  # noqa: F401
from . import profile as _profile_routes  # noqa: F401
from . import notifications as _notifications_routes  # noqa: F401
from . import transactions as _transactions_routes  # noqa: F401
