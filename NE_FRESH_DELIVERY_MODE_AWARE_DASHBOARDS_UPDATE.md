# NE FRESH Delivery Mode Aware Dashboard/Template Update

This update adds delivery-mode-aware display across customer checkout, customer orders, order tracking, store dashboard, store order management, store external delivery, admin dashboard, and admin external delivery pages.

## What changed

- Added global delivery mode UI helpers in `app_core.py`.
- Added order delivery mode decoration helpers so templates can safely show:
  - In-house delivery
  - External local delivery / Rapido-Zomato style
  - Third-party shipping / Shiprocket-courier style
- Added delivery-mode metrics for Admin and Store dashboards.
- Updated customer checkout to show active delivery mode, provider, fee label, payment rule and ETA.
- Updated customer My Orders page to show delivery mode/provider in desktop and app/mobile card view.
- Updated Track Order page support is preserved and backend API now returns external delivery fields.
- Updated Store Dashboard to show different delivery cards/actions depending on the active mode.
- Updated Store Order Management to show external/courier controls instead of in-house rider assignment for external orders.
- Updated Store External Delivery page into responsive card/table UI.
- Updated Admin Dashboard to show active delivery mode, mode counts, and mode-specific actions.
- Updated Admin External Delivery Orders page with active mode context.

## Existing logic preserved

- Product price/subtotal logic unchanged.
- Checkout final total formula preserved.
- Existing in-house delivery workflow preserved.
- Existing COD/online flow preserved.
- Existing store status update forms preserved.
- Existing cancel modal preserved.
- Existing return/refund behavior preserved.
- Existing external delivery fallback quote layer preserved.

## Delivery mode behavior

### IN_HOUSE
- Shows NE FRESH delivery-boy controls.
- Store Dashboard shows Delivery Control.
- Store Orders shows rider assignment/reassignment controls.
- Customer tracking shows in-house stages.

### EXTERNAL_LOCAL_DELIVERY
- Shows external local delivery wording.
- Checkout displays external provider/rule.
- Store Dashboard links to External Delivery.
- Store Orders shows external ready/booking panel instead of rider assignment.
- Customer tracking shows external local status/provider/tracking fields.

### THIRD_PARTY_SHIPPING
- Shows courier/Shiprocket wording.
- Checkout displays courier charge/rule.
- Store Dashboard links to External Delivery.
- Store Orders shows courier ready/booking panel instead of rider assignment.
- Customer tracking shows AWB/shipment/tracking fields.

## Validation

- Python compile check passed for `app_core.py`, `routes`, and `services`.
- Jinja syntax parse passed for top-level templates.
