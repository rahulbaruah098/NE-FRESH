# NE FRESH / NE LOCALS — Step 4 Low-Risk Service Extraction

## Status

Step 4 implements the blueprint's low-risk extraction stage on top of the Step 3 project that passed the user's Windows runtime gate with **41 passed / 0 failed**.

This step is an architectural move only. Existing route files, templates, form contracts, endpoint names, MongoDB document shapes, finance formulas, payment/refund/payout rules, inventory reservation logic, delivery settlement logic and UI behavior are intentionally unchanged.

## What moved out of `app_core.py`

### Product pricing
New file: `services/product_pricing.py`

- `_safe_float`
- `_calculate_product_pricing_from_form`

### Product units
New file: `services/product_units.py`

- `UNIT_OPTIONS`
- `UNIT_TYPE_LABELS`
- unit normalization, quantity rules and product hydration helpers
- `build_unit_product_update_from_form`

### Product bundles
New file: `services/product_bundles.py`

- `BUNDLE_DISCOUNT_TYPES`
- bundle discount normalization
- item snapshot building
- bundle pricing
- bundle stock calculation
- live bundle hydration
- bundle document/cart snapshot creation
- cart availability validation
- low-stock notification bridge

### Store categories
New file: `services/store_categories.py`

- `DEFAULT_STORE_CATEGORIES`
- category slug/default seeding/query/count helpers

### Store notifications
New file: `services/store_notifications.py`

- notification statistics
- creation/hydration
- order-notification synchronization

### Non-financial Store helpers

- `services/store_catalog.py` — Store product loading/hydration
- `services/store_profile.py` — profile completion/checklist context

### Shared helpers

- `helpers/formatting.py` — pincode/state/status/role/phone/order-status formatting
- `helpers/identifiers.py` — legacy/current ObjectId/string Store and Order identifier compatibility

## Compatibility strategy

`app_core.py` imports and re-exports every moved public/legacy helper name. Its existing dynamic `__all__` remains in place, so the current split route files can continue using `from app_core import *` unchanged during this phase.

No extracted service/helper imports `app_core.py` back. This prevents a new circular dependency and establishes the intended dependency direction:

`routes -> app_core compatibility bridge -> helpers/services -> extensions/MongoDB`

Later blueprint steps will replace route wildcard imports with explicit domain imports.

## Size reduction

- Step 3 `app_core.py`: **8,857 lines / 266 top-level functions**
- Step 4 `app_core.py`: **7,616 lines / 215 top-level functions**
- Reduction: **1,241 lines and 51 top-level functions**
- Extracted constants: **4**
- New low-risk helper/service modules: **9 functional modules**

The moved function ASTs and extracted constant ASTs were compared to the Step 3 source: **55/55 moved definitions are structurally identical**. This is deliberate evidence that behavior was moved rather than rewritten.

## Regression safety net

Added `tests/test_step4_low_risk_services.py` covering:

- module existence and dependency direction
- removal of duplicate ownership from `app_core.py`
- compatibility imports
- product percentage/fixed discounts
- unit normalization/quantity rules/product hydration
- bundle pricing and stock calculation
- category slug behavior
- shared formatting behavior
- legacy/current ID compatibility
- route-free Store service boundaries

The complete suite now collects **56 tests**.

In the build sandbox, Flask/PyMongo are unavailable, so the result is:

- **46 passed**
- **10 skipped** (runtime tests only, because Flask is not installed)
- **0 failed**

On the user's normal development environment, where Step 3 already produced 41/41 passing tests, the expected Step 4 gate is:

```powershell
python -m pytest
```

Expected result:

```text
56 passed
0 failed
```

The same third-party Razorpay and Python `datetime.utcnow()` deprecation warnings may remain; they are tracked separately and are not Step 4 behavior changes.

## Protected areas not touched

- No route module edited.
- No HTML template edited.
- No form changed.
- No URL/endpoint changed.
- No finance helper extracted or rewritten.
- No order stock reservation/release implementation changed.
- No Razorpay verification logic changed.
- No cancellation/refund/payout logic changed.
- No delivery settlement logic changed.
- No MongoDB field/collection migration performed.
- No UI or FOUC code changed.

## Next step

After the user's environment returns **56 passed / 0 failed**, proceed to **Step 5 — critical order / inventory / delivery service extraction** as defined by the blueprint. Step 5 must move state-transition and stock/delivery logic behind tests without changing externally visible behavior.
