# STEP 2 — Regression Safety Net

## Purpose
Step 2 adds a regression safety net **before any app_core.py/service extraction**. No application route, template, Mongo schema, form contract, payment rule, payout rule, stock rule, delivery rule or UI code was changed in this step.

## Source
Built directly on `NE-FRESH_Step1_Stability_Preflight_Full_Updated_Project.zip`, which itself came from the frozen user source `NE-FRESH-main (26).zip`.

## Added
- `pytest.ini`
- `requirements-dev.txt`
- `tests/support/` contract and source-extraction helpers
- Frozen route/build-only alias/form baselines
- Static route/form/role/source-health regression tests
- Executable pure Platform Fee, customer-money, Store payout, COD, refund and monthly Delivery Partner settlement tests
- Executable pure product and bundle stock reserve/release/rollback tests
- Critical source-contract tests for customer cancellation, Store cancellation visibility, Razorpay guards and Store-contained tracking
- Runtime Flask + mongomock tests for health endpoints, role isolation, dashboard smoke, customer cancellation + stock restoration, online refund transition, and Razorpay mismatch/idempotency

## Frozen Step 2 contract counts
- Route method contracts: **282**
- Build-only aliases: **2**
- Template form contracts: **182**

## Validation in this sandbox
- Python source parse: **55 files, 0 errors**
- Jinja parse: **104 templates, 0 errors**
- Static JavaScript syntax: **8 files, 0 errors**
- Static + pure pytest suite: **26 passed**
- Full pytest collection: **26 passed, 10 runtime tests skipped** because Flask/PyMongo are not installed in this sandbox and outbound package installation is unavailable.
- Existing Step 1 application files changed: **0**

## Run the complete suite on a development/staging machine
```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

The runtime suite uses `mongomock`, not the production MongoDB. It patches the current global `mongo` references in `app_core` and the loaded route modules to one in-memory test database.

## Step 3 gate
Do **not** begin infrastructure extraction from `app_core.py` until the complete `python -m pytest` run passes in an environment where `requirements-dev.txt` can be installed. This is deliberate: the blueprint requires the regression suite to be green before structural movement.
