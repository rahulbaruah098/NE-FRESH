# NE FRESH / NE LOCALS — Step 6 Finance Service Extraction

## Entry gate
Step 6 was started only after the user-run Windows Step 5 gate passed **70 passed / 0 failed**.

## Purpose
Step 6 implements the finance-service extraction defined in the approved backend/AWS blueprint. The goal is to move protected finance calculations and shared policy/reconciliation helpers into dedicated modules while preserving URLs, endpoint names, form contracts, MongoDB document contracts, UI templates, and the established financial ownership rules.

The Flask route handlers remain the HTTP/orchestration layer. `app_core.py` remains a compatibility bridge until the later route-decomposition stage.

## New finance modules

### `services/platform_fees.py`
Platform Fee settings/defaults, normalization, calculation, and canonical order money breakdown.

### `services/finance_reconciliation.py`
Customer/business payment reconciliation snapshots, COD collection ownership, unresolved-refund blocking, Store payout eligibility, and net Platform Fee interpretation.

### `services/store_finance_adjustments.py`
Store refund carry-forward adjustment creation, outstanding totals, oldest-first application, compare-and-set consumption, and rollback when payout finalization fails.

### `services/delivery_monthly_settlement.py`
India-month period calculation, period closure, Delivery Partner identity normalization, monthly payout model constants, and customer/business-money reconciliation gates.

### `services/payment_gateway.py`
Shared Razorpay TEST/LIVE environment-key resolution, checkout/server settings, client construction, Admin-safe credential status, and signature verification primitive. Razorpay secrets remain environment-only.

### `services/refund_policy.py`
Customer and Admin return/refund policy views.

### `services/finance_actions.py`
Pure state/calculation builders for Store Platform Fee remittance, COD-UPI verification, Rider COD Cash receipt, refund financial impact, Store payout base calculation, and monthly Delivery Partner payout calculation. This module performs no MongoDB writes.

## Compatibility strategy
- `app_core.py` imports and re-exports legacy finance names.
- Finance-heavy route files now import the Step 6 services explicitly where appropriate.
- No Step 6 service imports `app_core.py` back.
- Step 7 route decomposition has **not** begun.

## Exact extraction preservation
- 47 definitions/constants AST-compared against the Step 5 source.
- 37 came from `app_core.py`.
- 5 came from `routes/admin/routes.py`.
- 5 came from `routes/orders/routes.py`.
- **47/47 structurally identical; 0 unexpected rewrites.**

## Confirmed finance consistency hardening
1. **COD-UPI Admin verification** now uses a compare-and-set guard so repeated/concurrent verification cannot create duplicate audit/reconciliation effects.
2. **Rider COD Cash receipt** now uses a compare-and-set guard so duplicate receipt actions cannot create repeated settlement/audit effects.
3. **Refund finalization** now has a closed-status compare-and-set guard; transaction mirroring and carry-forward creation occur only after the authoritative order update succeeds.
4. An unreachable duplicate Store Platform Fee settlement block after an unconditional return was removed. This was dead code and does not change live behavior.

Existing Store Platform Fee remittance, external remittance, Store-direct collection, Store payout PROCESSING lock, carry-forward rollback, monthly rider period-close, and Razorpay idempotency protections remain intact.

## Protected finance rules preserved
- Prepaid customer money remains Admin/Platform money until Store payout.
- COD Cash held by a Delivery Partner is business money to submit, not rider earnings.
- COD UPI uses the official business UPI and requires Admin verification; rider cash liability remains zero.
- Store-direct collection does not create an Admin-to-Store payout; Platform Fee can remain due from Store.
- External Partner collection blocks Store payout until remittance is reconciled.
- Unresolved refund/return state continues to block Store payout.
- Refund before payout reduces the pending Store payout.
- Refund after Store payment creates/reuses a Store carry-forward adjustment.
- Delivery Partner monthly earning remains Delivery Fee + Tip only.
- `SELF_DEDUCTED` remains prohibited for current/new writes.

## Architecture result
- Step 5 `app_core.py`: **6,628 lines / 195 top-level functions**
- Step 6 `app_core.py`: **5,920 lines / 176 top-level functions**
- Step 6 reduction: **708 lines / 19 top-level functions**

Route-file line changes:
- `routes/admin/routes.py`: 10,155 -> 9,861
- `routes/orders/routes.py`: 3,851 -> 3,737
- `routes/store/routes.py`: 6,818 -> 6,820
- `routes/delivery/routes.py`: 2,995 -> 3,006
- `routes/external_delivery/routes.py`: 1,001 -> 1,002

## Contract preservation
- Route contracts: **282 / unchanged**
- Form contracts: **182 / unchanged**
- Build-only aliases: **2 / unchanged**
- Endpoint names changed: **0**
- UI templates changed: **0**

## Regression expansion
Step 6 adds finance-service ownership and runtime/pure tests covering Platform Fee transitions, COD-UPI state, Rider Cash state, refunds before/after payout, Store-direct refund carry-forward, cancelled-order refund behavior, Store payout base calculation, monthly Delivery Partner earnings, compare-and-set/idempotency behavior, carry-forward apply/rollback, monthly settlement idempotency, and Store Platform Fee idempotency.

## Packaging-environment validation
- Python syntax: 0 errors
- Jinja: 104 templates / 0 parse errors
- Static JavaScript: 8 files / 0 syntax errors
- Full suite collected: 95 tests
- Packaging environment: **74 passed / 21 runtime skipped / 0 failed** because Flask/PyMongo are unavailable in this environment

## Required Windows/dev runtime gate
From the Step 6 Full Updated Project, using the same virtual environment that passed Step 5:

```powershell
python -m pytest
```

Required result:

```text
95 passed
0 failed
```

Do **not** begin Step 7 route decomposition until this full runtime gate is green.
