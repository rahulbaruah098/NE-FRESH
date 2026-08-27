# NE FRESH — Step 3 Infrastructure Extraction

## Status
Step 3 follows the approved backend architecture / AWS EC2 blueprint. The Step 2 regression gate was confirmed on the user's Windows development environment with **36 passed, 0 failed** before this refactor began.

## Objective
Move infrastructure concerns out of `app_core.py` without changing routes, endpoint names, forms, MongoDB document contracts, financial rules, UI behaviour or role flows. `app_core.py` remains a compatibility bridge in this phase.

## New infrastructure modules
- `config.py` — environment parsing, secret/session policy, CORS origin config, production sender warnings.
- `logging_config.py` — compatibility logging entry points.
- `security.py` — CORS registration, CSRF helpers/protection and shared no-cache response hook.
- `uploads.py` — upload directory/max-size configuration and `allowed_file()`.
- `template_context.py` — site-wide Jinja context processors and static footer/brand context.
- `extensions.py` — stable shared Mongo resource import path.
- `database_init.py` — explicit DB bootstrap/admin seed orchestration; no import-time mutation.

## Existing files changed
- `app_core.py`
  - Flask app object remains here for compatibility.
  - Infrastructure setup now delegates to the extracted modules.
  - Context processors are registered after the legacy business providers have been defined.
  - Existing wildcard-compatible exports remain enabled through `__all__`.
- `scripts/init_db.py`
  - No longer imports `app_core.py`.
  - Uses `database_init.initialize_database()` and `extensions.mongo` directly.
- `tests/test_step3_infrastructure_extraction.py`
  - Adds architecture regression checks for this phase.

## app_core.py reduction
- Before Step 3: **9,145 lines**, 284 top-level function/class definitions.
- After Step 3: **8,857 lines**, 266 top-level function/class definitions.
- Net movement: **288 lines removed from the monolith** in this phase.

This is intentionally not an aggressive reduction. Product, order, inventory, delivery and finance services remain in `app_core.py` until their dedicated blueprint phases and regression gates.

## Compatibility guarantees
The Step 2 source-contract baselines still pass exactly:
- 282 route/method contracts — unchanged.
- 182 form contracts — unchanged.
- 2 build-only endpoint aliases — unchanged.
- No endpoint rename.
- No URL path change.
- No form method/action/input-name change.
- No MongoDB schema migration.
- No finance/order/inventory calculation change.
- No UI/template redesign.

## Validation in this environment
- All Python files compile: PASS.
- 104 Jinja templates parse: PASS.
- 8 static JavaScript files pass syntax checking: PASS.
- Step 2 + Step 3 static/pure regression suite: **31 passed**.
- 10 runtime tests are skipped only because this sandbox does not contain Flask/PyMongo.

## Required runtime gate on the development machine
After extracting the Step 3 full project, run:

```powershell
python -m pytest
```

Expected result with the already-installed development dependencies:

```text
41 passed
0 failed
```

The existing deprecation warnings may remain; they are tracked separately and are not Step 3 failures.

## What remains intentionally unchanged for later phases
- `app.py` still imports the global Flask app from `app_core.py`.
- Route modules still use `from app_core import *` as a compatibility bridge.
- Giant route modules are not split yet.
- Business services have not yet been extracted.
- The app factory / WSGI architecture is not introduced until the blueprint's later step.

This sequencing is deliberate to keep every stage reversible and regression-testable.
