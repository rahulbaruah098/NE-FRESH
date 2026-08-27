# NE FRESH - Step 7 Route Decomposition

## Status

Step 7 implements the blueprint work package **Split giant route modules**. The Step 6 application contract is preserved while the four largest role/domain route files are decomposed into smaller domain route modules.

## Source of truth

This step was built directly on the validated **Step 6 Finance Service Extraction Full Updated Project**. The user's Windows gate for Step 6 completed with **95 passed / 0 failed**.

## What changed

The previous giant files are now compact registries:

- `routes/admin/routes.py`
- `routes/store/routes.py`
- `routes/orders/routes.py`
- `routes/delivery/routes.py`

Each registry loads:

1. `shared.py` for the existing imports, constants, hooks and legacy route helpers that have not yet been extracted into services; and
2. smaller domain route files containing the original `@app.route` functions.

No Flask Blueprint namespacing was introduced in this step. This is deliberate: existing endpoint names must remain exactly the same until the application-factory stage is ready.

## New domain modules

### Admin

- `settings.py`
- `refunds.py`
- `settlements.py`
- `dashboard.py`
- `notifications.py`
- `stores.py`
- `delivery_management.py`
- `user_exports.py`
- `complaints.py`
- `users.py`
- `contact_messages.py`
- `profile.py`

### Store

- `public_storefront.py`
- `dashboard_settings.py`
- `products.py`
- `orders.py`
- `delivery_management.py`
- `returns.py`
- `inventory.py`
- `categories.py`
- `reviews.py`
- `complaints.py`
- `profile.py`
- `notifications.py`
- `transactions.py`

### Orders

- `customer_actions.py`
- `checkout.py`
- `payments.py`
- `history_tracking.py`
- `api.py`

### Delivery

- `profile_support.py`
- `dashboard.py`
- `orders.py`
- `earnings.py`
- `actions.py`
- `tracking.py`

## Compatibility design

`app.py` is intentionally unchanged and still imports only the four registry modules. Each domain module imports the role's `shared.py` compatibility namespace. This avoids circular imports and preserves the same Flask `app` object, decorators, endpoint names, URL paths, session/auth helpers and Mongo/service objects.

The `shared.py` files are transitional. They are not a new permanent monolith; they contain the non-route helpers that were already present in the previous giant route files. Later cleanup can move those helpers into services/repositories without mixing that work into route decomposition.

## Integrity safeguards

- All 170 route functions moved from Admin/Store/Orders/Delivery are AST-hashed against the Step 6 implementation.
- Result: **170 / 170 route functions structurally identical**.
- Frozen live route contract remains **282** entries.
- Frozen form contract remains **182** entries.
- Build-only aliases remain **2**.
- No duplicate live route/path-method pairs.
- No missing literal `url_for()` endpoint references.
- `app_core.py`, all service modules, all templates and all static assets remain unchanged by Step 7.

## Test-suite updates

Older source-contract tests that intentionally inspected the former giant file locations were updated to inspect the new canonical domain/shared locations. Their assertions were not weakened; only the expected file locations changed.

Step 7 also adds:

- static decomposition structure tests;
- exact AST regression protection for all 170 moved route functions; and
- runtime checks proving that all frozen routes register and representative endpoints resolve to the new domain modules.

## Local runtime gate

Run from the project root:

```powershell
python -m pytest
```

Expected Step 7 result on the user's configured Windows environment:

```text
105 passed
0 failed
```

Warnings about `datetime.utcnow()` and Razorpay's `pkg_resources` deprecation are tracked technical debt and are not Step 7 failures.

## Next blueprint stage

After the Step 7 runtime gate is green, proceed to **Step 8 - Application Factory + WSGI Entry**. That stage will introduce deterministic `create_app()` / `wsgi.py` bootstrapping and prepare the application for multi-worker Gunicorn startup without repeated database mutation.
