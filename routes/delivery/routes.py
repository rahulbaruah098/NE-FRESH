"""Delivery route registry.

Step 7 splits the previous giant route module into domain files without
changing Flask endpoint names, URL paths, methods or business behaviour.
``app.py`` continues importing this module, so the external bootstrap contract
is unchanged.
"""

# Load shared hooks/helpers first, then import each domain module so its
# existing @app.route decorators register on the same Flask application.
from . import shared as _shared  # noqa: F401

from . import profile_support as _profile_support_routes  # noqa: F401
from . import dashboard as _dashboard_routes  # noqa: F401
from . import orders as _orders_routes  # noqa: F401
from . import earnings as _earnings_routes  # noqa: F401
from . import actions as _actions_routes  # noqa: F401
from . import tracking as _tracking_routes  # noqa: F401
