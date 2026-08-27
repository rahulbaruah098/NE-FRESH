"""Orders route registry.

Step 7 splits the previous giant route module into domain files without
changing Flask endpoint names, URL paths, methods or business behaviour.
``app.py`` continues importing this module, so the external bootstrap contract
is unchanged.
"""

# Load shared hooks/helpers first, then import each domain module so its
# existing @app.route decorators register on the same Flask application.
from . import shared as _shared  # noqa: F401

from . import customer_actions as _customer_actions_routes  # noqa: F401
from . import checkout as _checkout_routes  # noqa: F401
from . import payments as _payments_routes  # noqa: F401
from . import history_tracking as _history_tracking_routes  # noqa: F401
from . import api as _api_routes  # noqa: F401
