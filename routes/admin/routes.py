"""Admin route registry.

Step 7 splits the previous giant route module into domain files without
changing Flask endpoint names, URL paths, methods or business behaviour.
``app.py`` continues importing this module, so the external bootstrap contract
is unchanged.
"""

# Load shared hooks/helpers first, then import each domain module so its
# existing @app.route decorators register on the same Flask application.
from . import shared as _shared  # noqa: F401

from . import settings as _settings_routes  # noqa: F401
from . import refunds as _refunds_routes  # noqa: F401
from . import settlements as _settlements_routes  # noqa: F401
from . import dashboard as _dashboard_routes  # noqa: F401
from . import notifications as _notifications_routes  # noqa: F401
from . import stores as _stores_routes  # noqa: F401
from . import delivery_management as _delivery_management_routes  # noqa: F401
from . import user_exports as _user_exports_routes  # noqa: F401
from . import complaints as _complaints_routes  # noqa: F401
from . import users as _users_routes  # noqa: F401
from . import contact_messages as _contact_messages_routes  # noqa: F401
from . import profile as _profile_routes  # noqa: F401
