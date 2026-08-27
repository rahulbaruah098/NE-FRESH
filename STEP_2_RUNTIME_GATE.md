# Step 2 Runtime Gate

## Prepared runtime tests
1. `/health/live` returns alive.
2. `/health/ready` verifies Mongo + writable uploads.
3. Store account hitting the customer order-track URL is redirected into the Store tracker.
4. Admin and Delivery dashboards do not return HTTP 500 with empty in-memory fixtures.
5. Store dashboard does not return HTTP 500 with a minimal Store fixture.
6. COD customer cancellation restores stock and voids the unpaid finance leg.
7. Paid online customer cancellation keeps payment paid and creates `READY_FOR_REFUND` / `REFUND_PENDING` state for Admin processing.
8. Razorpay verification rejects a mismatched Razorpay order ID before gateway signature verification.
9. A previously paid order is idempotently treated as already verified rather than duplicated.

## Current sandbox status
Runtime execution is **pending**, not failed. Flask/PyMongo are unavailable in this sandbox and package installation cannot reach PyPI. The runtime tests therefore skip cleanly here.

## Required gate command
```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

Proceed to Step 3 only after this command returns zero failures/skips caused by missing runtime dependencies.
