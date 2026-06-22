# NE FRESH Simplified Auto Delivery Routing Update

This update restarts the delivery logic into a simpler and more practical model while preserving the existing in-house delivery-boy workflow.

## Final Business Logic

### 1. In-house Delivery
- Existing delivery-boy system remains available.
- Existing store zone/serviceability, delivery assignment, live tracking, delivery-boy COD/POD settlement and in-house delivery fee settings remain intact.
- Checkout uses in-house delivery first when the customer is serviceable by the store's existing in-house rules.

### 2. External Local Delivery
- Used for local partner delivery such as Rapido-style/Ola-style/Uber-style/manual local pickup-drop.
- No live Rapido/Ola/Uber API booking is created.
- No external local booking dashboard or external local partner payment/remittance records are maintained.
- Checkout calculates a hard-coded assumed local fare from Admin settings:
  - Base fee
  - Per km fee
  - Minimum fee
  - Maximum local distance
  - Optional local zone polygon
- Customer receives the NE FRESH order reference as the local delivery reference.

### 3. Shiprocket / Courier Shipping
- Used automatically when in-house and external local delivery are not serviceable and Shiprocket/courier channel is enabled.
- Shiprocket/courier is forced to online payment only to avoid COD remittance conflicts.
- Store marks the package ready, then the system attempts Shiprocket booking using saved credentials.
- If Shiprocket booking fails, Admin can retry from Shiprocket Shipments.

## Payment Logic

The customer payable amount remains:

```text
Items Subtotal + Delivery Fee + Platform Fee + Tip
```

- Platform fee logic is unchanged.
- In-house delivery fee uses existing in-house fee/zone/slab logic.
- External local delivery fee uses hard-coded local fare rules.
- Shiprocket/courier delivery fee uses Shiprocket quote if available, otherwise courier fallback fee.
- Backend still uses the internal value `COD` for compatibility, but the UI now presents it as `Pay on Delivery`.

## Why This Was Changed

The previous single active delivery mode was not practical because nearby and outstation customers can order at the same time. Store-side selection after payment was also risky because the delivery fee could become wrong after the customer had already paid.

The new Auto Hybrid Routing decides the delivery route at checkout before payment, so the correct delivery fee is charged before the order is placed.

## Updated/Renamed Templates

- `admin_delivery_mode_settings.html` was replaced by `admin_delivery_routing_settings.html`.
- `admin_external_delivery_settings.html` was replaced by `admin_delivery_channel_settings.html`.
- `admin_external_delivery_orders.html` was replaced by `admin_shiprocket_shipments.html`.
- `store_external_delivery.html` was replaced by `store_shiprocket_shipments.html`.

Route endpoint names were preserved for compatibility where possible.
