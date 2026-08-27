# NE FRESH / NE LOCALS — Step 1 Stability Cleanup + Production Preflight

Source of truth: `NE-FRESH-main (26).zip` frozen in Step 0.

## Scope

Step 1 intentionally changes only stability/deployment-preflight concerns. It does **not** refactor finance services, split the giant route modules, rename live URLs, change form contracts, change Mongo document shapes, redesign UI, or alter payment/refund/payout calculations.

## Implemented

1. Removed import-time MongoDB mutation from `app_core.py`.
   - `ensure_mongo_indexes()` no longer runs while workers import the app.
   - `ensure_admin_seed_password()` no longer runs while workers import the app.
   - Database setup is now explicit through `python scripts/init_db.py`.

2. Removed the duplicate `_safe_float()` definition from `app_core.py`.

3. Fixed duplicate live public routes without breaking old `url_for(...)` names.
   - `/help` now has one live GET handler: `help_page`.
   - `/report-fraud` now has one live GET handler: `report_fraud_page`.
   - `legal_help` and `legal_report_fraud` remain build-only compatibility aliases, so existing URL generation still produces the same public URLs.

4. Added production health endpoints.
   - `GET /health/live` — proves a Flask worker can answer HTTP.
   - `GET /health/ready` — checks MongoDB ping and writable local upload storage; returns 503 when not ready.

5. Added explicit deployment/config preflight.
   - `python scripts/validate_config.py`
   - `python scripts/validate_config.py --production`
   - The script does not print secrets and fails on unsafe/missing production-critical settings.

6. Added explicit database initialization.
   - `python scripts/init_db.py`
   - `python scripts/init_db.py --check-only`
   - `python scripts/init_db.py --skip-admin-seed`
   - `python scripts/init_db.py --skip-indexes`

7. Mongo client startup is now intentionally lazy and uses bounded connection/selection timeouts controlled by:
   - `MONGO_SERVER_SELECTION_TIMEOUT_MS`
   - `MONGO_CONNECT_TIMEOUT_MS`

8. Replaced the obsolete nested SQLite `.env.example` with a current root `.env.example` covering MongoDB, sessions/CSRF, CORS, persistent uploads, SMTP, Razorpay and optional admin bootstrap.

9. Removed verified stale/non-live artifacts:
   - `routes/admin/routes.py.bak`
   - `package-lock.json` (empty lockfile with no `package.json`)
   - broken/unreferenced `static/app.js`
   - old unimported Promotions implementation and its template/CSS/service
   - stale Store bundles/deals/offers templates and their CSS that referenced endpoints absent from the live route inventory
   - legacy `ne-fresh-mvp/.env.example`

## Validation completed

- Python: 39 current `.py` files, 0 syntax errors.
- Jinja: 104 current HTML templates, 0 parse errors.
- JavaScript: 8 current static JS files, 0 syntax errors.
- Live route modules imported by `app.py`: 13 including the new health module.
- Live route decorators: 260.
- Duplicate live path/method pairs: 0.
- Literal template `url_for(...)` references unresolved: 0 when build-only compatibility aliases are included.
- Missing literal static references: 0.
- All 97 Step 0 live-template dependency files remain present and byte-identical, so the 171 live form contracts are unchanged.
- No business/finance template was modified.
- No payment, payout, refund, stock, delivery settlement or reconciliation formula was modified.

## Runtime boundary

The current sandbox does not have Flask/PyMongo installed and does not have the project's real MongoDB/external-provider environment. Therefore Step 1 health routes and `scripts/init_db.py` were syntax/static validated but not executed against a real database here. That live execution becomes part of the Step 2/staging regression gates.

## Next blueprint step

Step 2: build the automated regression safety net before moving business logic out of `app_core.py`.
