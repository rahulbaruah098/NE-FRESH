# NE FRESH External Provider Settings UI Restructure

Updated only the external provider settings area and the supporting settings fields.

## What changed

- Renamed the page meaning clearly as External Provider Settings.
- Reorganized the page into understandable sections:
  1. Provider connection settings
  2. Fallback delivery charge rules
  3. Pickup map and optional external service zone
  4. Default package dimensions
- Added a Leaflet map for:
  - default external pickup pin
  - optional external service zone polygon
- Added backend saving for:
  - external_default_pickup_latitude
  - external_default_pickup_longitude
  - external_service_zone_enabled
  - external_service_zone_polygon
- Added optional checkout enforcement for external service zone only when enabled.

## What was not changed

- Existing in-house delivery logic was not changed.
- Existing store delivery-boy assignment logic was not changed.
- Existing payment/platform fee calculation logic was not changed.
- Existing external delivery order dashboard/actions were not changed.

## Important

The external service zone is optional. If it is not enabled, checkout continues using the already existing store/serviceability and fee rules.
