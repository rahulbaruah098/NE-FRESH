# Step 8 — Application Factory + WSGI + Deterministic Startup

## Status
Step 8 implementation is complete and ready for the Windows runtime gate.

## Goal
Introduce a single deterministic application bootstrap that can be used by both local development and a production WSGI server without changing any frozen route, form, finance, inventory, delivery, MongoDB document, or UI contract.

## What changed

### 1. `app_factory.py` added
`create_app()` is now the central application startup contract. It:

- creates/configures the Flask app once per Python worker process;
- applies the existing configuration, CORS/CSRF hooks and upload settings;
- imports `app_core.py` only after the configured base app exists;
- imports the same 13 route registries in the same order used before Step 8;
- is idempotent: repeated calls do not duplicate routes, context processors or security hooks;
- does **not** run MongoDB indexes, seeds or other database mutations.

The current route layer still uses direct legacy `@app.route` decorators. For that reason Step 8 intentionally uses one process-local app instance rather than pretending that multiple isolated Flask app objects can be constructed safely. Gunicorn workers are separate processes, so each worker receives its own deterministic process-local instance.

### 2. `app.py` reduced to the local-development entry point
`app.py` now exports:

```python
from app_factory import create_app
app = create_app()
```

When run directly it delegates the development server/reload behaviour to `development.py`.

### 3. `wsgi.py` added
Production WSGI servers now have a stable target:

```text
wsgi:app
```

`wsgi.py` does not call Flask's development server and does not enable development reload hooks.

### 4. `development.py` added
The existing optional local auto-reload/browser-refresh behaviour was moved out of production bootstrap. It remains available through:

```powershell
$env:NEFRESH_AUTO_RELOAD="1"
$env:FLASK_DEBUG="1"
python app.py
```

### 5. `app_core.py` no longer constructs Flask
`app_core.py` now obtains the configured compatibility app from `app_factory.get_base_app()`. It still exports the same shared legacy names used by the split route modules.

The direct `app_core.py` development server block was removed. Startup ownership is now explicit:

- local development: `python app.py`
- production WSGI: `wsgi:app`
- database indexes/seeding: `python scripts/init_db.py`

## What did NOT change

- 282 frozen live route contracts
- 182 frozen form contracts
- 2 build-only endpoint aliases
- endpoint names or URL paths
- Admin/Store/Customer/Delivery role boundaries
- order/inventory/delivery state logic
- Razorpay logic
- COD Cash / COD UPI ownership logic
- Platform Fee, payouts, refunds or reconciliation
- MongoDB collection/document contracts
- templates, CSS or JavaScript

## Database startup safety
The application factory contains no call to:

- `initialize_database()`
- `ensure_mongo_indexes()`
- `ensure_admin_seed_password()`

Database initialization remains an explicit operator action through `scripts/init_db.py`, so Gunicorn worker creation cannot repeat index/seed mutations.

## Tests added
Step 8 adds 12 regression checks:

- 9 source/architecture tests
- 3 Flask runtime tests

The runtime tests verify:

1. repeated `create_app()` calls return the same process-local app without duplicate routes;
2. `wsgi.app`/`wsgi.application` expose the fully registered application;
3. repeated factory calls do not duplicate before-request, after-request or Jinja context hooks.

## Validation in packaging environment

```text
117 tests collected
91 passed
26 skipped (runtime dependencies unavailable in packaging environment)
0 failed
```

The required Windows gate is:

```powershell
python -m pytest
```

Expected:

```text
117 passed
0 failed
```

## Step 9 boundary
Step 8 intentionally does **not** add Gunicorn/systemd/Nginx deployment packaging. Those belong to Step 9. Step 8 only establishes the deterministic WSGI/application startup contract that Step 9 will deploy.
