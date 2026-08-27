# Step 1 — Broad Exception Handling Classification

This is a classification pass, not a mass rewrite. Broad exception blocks were not mechanically replaced because many are intentional fallback boundaries and changing them without regression tests could destabilize production flows.

## Current count after Step 1 cleanup

- Broad `except Exception` / bare-except handlers: **371**
- Handlers whose body is only `pass`: **69**

Largest concentrations:

- `app_core.py`: 98 broad / 16 silent-pass
- `routes/admin/routes.py`: 94 / 23
- `routes/store/routes.py`: 58 / 7
- `routes/delivery/routes.py`: 22 / 8
- `routes/orders/routes.py`: 20 / 3

## Classification

### A. Safe conversion/parsing fallbacks

Examples include optional `ObjectId` coercion, numeric parsing, date parsing, and tolerant formatting. A failed optional conversion falls back to string/default handling. These should generally remain compact and do not need error logs for every malformed historical value.

### B. Optional UI/metric enrichment

Some handlers protect non-essential dashboard metrics, review/zone decoration, or historical-row formatting. Failure should not take down the underlying page. These may remain fallbacks but should gain structured diagnostics later where useful.

### C. Secondary notification/cleanup operations

Some order/delivery transitions perform the primary state mutation first and then try to create a secondary notification or cleanup record. Those fallbacks must be regression-tested before deciding whether to retry/log/escalate; they must not be blindly converted into fatal errors.

### D. External provider/email boundaries

Razorpay initialization/verification, delivery providers and SMTP already contain explicit failure returns/raises in important paths. These need integration tests and structured logging, not a blanket exception-type replacement.

### E. Critical state/finance paths requiring Step 2 tests before hardening

Priority regression targets include:

- Razorpay order creation and payment verification
- customer/Store cancellation + stock restoration
- Store order status transitions
- Store payout paid/reconciliation actions
- refund processing / return settlements
- external-partner remittance and Store-direct payment receipt
- Delivery Partner assignment/status/cancellation
- COD Cash / COD UPI reconciliation
- monthly Delivery Partner settlement

## Step 1 decision

Do not change the behaviour of these critical flows yet. Step 2 will first create route/finance/inventory/idempotency regression tests; later extraction phases can then replace overly broad catches with specific exception types and structured logging safely.
