# NE FRESH / NE LOCALS — Step 5 Protected Order, Inventory & Delivery Service Extraction

## Purpose

Step 5 follows the approved backend/AWS blueprint after Step 4 passed its full local regression gate (56 passed / 0 failed).

This step extracts protected order, stock and delivery operations into dedicated service modules while preserving all existing routes, endpoint names, form contracts, MongoDB collection/document contracts, financial rules and UI behavior.

## New modules

- `helpers/numbers.py`
  - shared delivery numeric parsing helpers
- `services/order_inventory.py`
  - product and bundle-child stock reservation/restoration
- `services/order_lifecycle.py`
  - customer cancellable statuses
  - Store order visibility / active-order classification
  - Delivery status transition rules
- `services/order_tracking.py`
  - read-only order tracking hydration and map coordinates
- `services/delivery_operations.py`
  - delivery availability
  - distance helpers
  - delivery-order hydration
  - order timeline events
  - delivery partner lookup
  - nearby online rider lookup
  - assignment / reassignment
  - assignment clearing

## Compatibility strategy

`app_core.py` imports and re-exports the extracted legacy names. Existing route modules that still use `from app_core import *` therefore continue to resolve the same names until the later explicit-import / route-splitting stage.

The new service modules do **not** import `app_core.py` back.

## Route-level changes

### Orders

`routes/orders/routes.py` now imports the stock reservation/restoration functions from `services/order_inventory.py`. The original stock functions were removed from the route file without changing their implementation.

### Store

`routes/store/routes.py` now uses the shared lifecycle service for Store-visible and active-order decisions.

A real stock-consistency defect was also corrected: Store cancellation previously restored only a direct `product_id`. Bundle order lines store their real product movements inside `bundle_items_snapshot`, so Store cancellation could fail to restore bundle child stock. Store cancellation now calls the same shared `_release_order_stock_items(...)` service already used by customer cancellation. This keeps normal-product and bundle cancellation behavior aligned and remains exactly-once because already-cancelled orders are blocked before the restore path.

### Delivery

`routes/delivery/routes.py` now uses the shared Step 5 delivery transition policy rather than keeping a duplicate local transition map. The allowed statuses and transition mapping are identical to the Step 4 source.

## Protected behavior preserved

- Customer cancellation only before shipment-ready / delivery assignment.
- Cancellation restores reserved stock once.
- Bundle stock reserve/release uses child quantity × ordered bundle quantity.
- Store active-order page excludes the same 14 terminal statuses as before.
- Unpaid online orders remain hidden from Store operational queues.
- Delivery assignment requires an active Delivery Partner and an assignable order state.
- A second assignment to the same rider is idempotent.
- Another rider cannot steal a normally assigned order.
- Delivery assignment clear returns the order to `SHIPMENT_READY` before pickup/out-for-delivery.
- Existing order tracking coordinate fallback/swap behavior is preserved.
- Delivery status transition rules are unchanged.

## Deliberately NOT changed in Step 5

- Razorpay calculations or verification logic
- COD Cash / COD UPI finance rules
- Platform Fee calculations
- Store payout calculations or blocking rules
- Refund/carry-forward calculations
- Delivery Partner monthly settlement calculations
- MongoDB schema/collection names
- Route paths or endpoint names
- Forms or CSRF contracts
- UI/templates/styles

Those finance concerns remain protected for Step 6.

## Architecture result

Step 4 `app_core.py`: 7,616 lines / 215 top-level functions

Step 5 `app_core.py`: 6,628 lines / 195 top-level functions

Reduction in Step 5: 988 lines and 20 top-level function definitions removed from `app_core.py`.

35 moved definitions/constants were AST-compared with the Step 4 source and are structurally identical.

## Validation performed in packaging environment

- Python source parse/compile: PASS
- Jinja parse: 104 templates / 0 errors
- Static JavaScript syntax: 8 files / 0 errors
- Route contract baseline: 282 / unchanged
- Form contract baseline: 182 / unchanged
- Build-only aliases: 2 / unchanged
- Static + pure pytest suite: 55 passed
- Runtime pytest cases: 15 prepared, skipped only because Flask/PyMongo are unavailable in the packaging environment
- ZIP fresh-extraction/hash integrity: required before release packaging

## Local runtime gate

Run from the Step 5 full project in the same Windows virtual environment that passed Step 4:

```powershell
python -m pytest
```

Expected result:

```text
70 passed
0 failed
```

Warnings about `datetime.utcnow()` and Razorpay `pkg_resources` are currently tracked compatibility/deprecation warnings and are not Step 5 failures.

Do not start Step 6 until the full Step 5 runtime gate is green.
