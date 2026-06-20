# NE FRESH External Delivery Mode Update Notes

This package adds the blueprint implementation layer for three delivery modes while preserving existing in-house delivery logic.

## Added Modes

1. `IN_HOUSE`
   - Existing NE FRESH delivery-boy panel, store assignment, live tracking, rider COD settlement, and current return/refund flow.

2. `EXTERNAL_LOCAL_DELIVERY`
   - Rapido/Ola/Uber/Shiprocket Quick style hyperlocal delivery preparation.
   - Orders move to the new External Delivery dashboard for booking/recording.

3. `THIRD_PARTY_SHIPPING`
   - Shiprocket/BlueDart/Delhivery courier-style shipping preparation.
   - Orders support external shipment ID, AWB, tracking URL, label URL, and webhook updates.

## Added Pages

Admin:
- `/admin/delivery-mode-settings`
- `/admin/external-delivery-settings`
- `/admin/external-delivery-orders`

Store:
- `/store/external-delivery`

Webhook:
- `/api/external-delivery/webhook/<provider>`

## Payment Behavior

- In-house mode keeps current COD and online payment behavior.
- External modes support configurable payment rules:
  - `ONLINE_ONLY` recommended first phase.
  - `COD_STORE_COLLECTION` supported as a stored settlement flag.
  - `COD_PARTNER_COLLECTION` supported as a stored settlement flag for future partner remittance reconciliation.

## Important

Live Shiprocket/Rapido booking needs real partner credentials and account-specific field mapping.
If credentials are missing, the system safely records a manual external booking instead of breaking order flow.

`.env`, `.git`, `__pycache__`, virtualenvs, and node_modules are excluded from this ZIP for safety and size.
