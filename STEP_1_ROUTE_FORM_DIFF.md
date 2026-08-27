# Step 1 — Route / Form Contract Diff

## Approved route changes

### Duplicate-path correction

Before Step 1, both of these public paths had two live GET rules:

- `/help`: `legal_help` + `help_page`
- `/report-fraud`: `legal_report_fraud` + `report_fraud_page`

After Step 1:

- `/help` has one live GET handler: `help_page`.
- `/report-fraud` has one live GET handler: `report_fraud_page`.
- `legal_help` and `legal_report_fraud` remain registered as `build_only=True` URL-building aliases for backward compatibility with existing `url_for(...)` calls.

This removes ambiguous request matching while preserving the public URLs and legacy URL-generation endpoint names.

### New health endpoints

- `GET /health/live` -> endpoint `health_live`
- `GET /health/ready` -> endpoint `health_ready`

### All other live route contracts

No other baseline live route path/method/endpoint tuple was intentionally changed.

## Form contracts

Step 1 did not modify any live template. All 97 templates in the Step 0 live-template dependency closure are byte-identical to baseline.

Therefore the Step 0 live form inventory remains unchanged:

- 171 live forms total
- methods/actions/input names/values unchanged
- CSRF form behaviour unchanged

Deleted templates were verified non-live stale candidates and were not part of the Step 0 live-template closure.
