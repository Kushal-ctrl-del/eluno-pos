# Eluno POS submission checklist

Audit basis: `brief/CANDIDATE_BRIEF.md`, the application entry points, the
deterministic mock server, seeded data, and the test suite. This checklist does
not claim production readiness or exactly-once behavior for external systems.

## Must requirements

| Requirement | Implementation | Evidence | Status |
|---|---|---|---|
| Customer lookup by phone and latest valid prescription | `app/application/customer_prescription.py`, `app/adapters/shopify_client.py`, `app/adapters/smt_client.py` | `tests/integration/test_customer_prescription_api.py`; seeded end-to-end audit | **Complete** |
| Frame/lens cart and prescription-compatible lens validation | `app/domain/cart.py`, `app/domain/prescription.py`, `app/application/customer_prescription.py` | `tests/unit/test_customer_prescription.py`; seeded end-to-end audit | **Complete** |
| Data-driven offers, conditions, dates and stackability | `app/domain/offers.py` | `tests/unit/test_offers.py`; seeded `offers.json` used in end-to-end audit | **Complete** |
| Inclusive GST by HSN, line-level CGST/SGST and paisa reconciliation | `app/domain/tax.py`, `app/adapters/shopify_orders.py` | `tests/unit/test_tax.py`; checkout payload tests; seeded end-to-end audit | **Complete; HSN is persisted as a line property** |
| Terminal payment followed by Shopify order with inventory decrement, tax, discounts, transaction, prescription and store invoice number | `app/application/checkout.py`, `app/adapters/terminal_client.py`, `app/adapters/shopify_orders.py` | `tests/unit/test_checkout.py`; `tests/integration/test_refunds_api.py`; seeded mock-server checkout audit | **Complete on supplied mock** |
| Full Shopify refund with tax reversal, frame restock, lens no-restock and sale transaction linkage | `app/application/refunds.py`, `app/adapters/shopify_orders.py` | `tests/unit/test_refunds.py`, `tests/integration/test_refunds_api.py`; seeded order refund audit | **Complete on supplied mock** |
| Store/date Shopify-backed day-end report with sales, tax, discounts, refunds and payment split | `app/application/reporting.py`, `app/domain/reporting.py`, `app/adapters/shopify_orders.py` | `tests/unit/test_reporting.py`, `tests/integration/test_reporting_api.py`; seeded order/refund audit | **Complete on supplied mock** |

## Should requirements

| Requirement | Implementation | Evidence | Status |
|---|---|---|---|
| Cash till sessions, float, cash sales/refunds, paid-ins, paid-outs, close and variance | `app/domain/till.py`, `app/application/till.py`, `app/persistence/database.py`, `app/entrypoints/cli.py` | `tests/unit/test_till.py`, `tests/unit/test_database.py`; CLI commands `till-open`, `till-movement`, `till-close` | **Complete for assignment scope** |
| Sales channel on every order and channel report | `app/domain/sales_channel.py`, `app/application/checkout.py`, `app/adapters/shopify_orders.py`, `app/domain/reporting.py`, `app/entrypoints/cli.py` | `tests/unit/test_sales_channel.py`, checkout/reporting unit tests, `tests/integration/test_reporting_api.py` | **Complete for supported channels; legacy/missing values reported as `unknown`** |
| Partial frame-only or lens-only refund | `app/application/refunds.py` (`refund_partial`) and Shopify refund adapter | `tests/unit/test_refunds.py`, `tests/integration/test_refunds_api.py` | **Complete on supplied mock** |
| Printable invoice | `app/application/invoices.py`, `app/adapters/shopify_orders.py`, `app/entrypoints/cli.py` | `tests/unit/test_invoices.py`, `tests/integration/test_invoices_api.py` | **Complete (plain text; includes HSN, GST rate labels, amount in words and sample note; no PDF/printer/IRN)** |
| Audit trail with actor, timestamp and reason code | `app/persistence/audit.py`, `app/persistence/database.py`, integrated with till, checkout and refund services | `tests/unit/test_audit.py`; manager-discount checkout test; complete checkout/refund/till suites | **Complete for implemented POS actions; manager reason is retained** |

## Submission and operational checks

- **Application composition:** `app/composition.py` wires customer/prescription,
  checkout, refund and reporting services.
- **CLI:** `app/entrypoints/cli.py` exposes `health-check` and
  `day-end-report`. Checkout and refund are demonstrable through the composed
  application services and integration harnesses, but do not currently have
  dedicated CLI subcommands.
- **End-to-end seeded workflow:** Passed against the deterministic mock:
  customer `80001005`, prescription `RX5007`, Shopify order `99000005`, one
  completed full refund, and a report containing one bill and one refund.
- **Duplicate protection tested:** Replaying the same checkout returned the
  existing completed state and left one Shopify order for the checkout
  reference. Replaying the same refund request returned the existing completed
  state and did not issue another refund operation.
- **Retry/recovery tests:** Terminal 503 retry uses the same order reference;
  uncertain terminal and Shopify outcomes are persisted and not blindly
  retried. The mock does not provide terminal refund-status lookup or a refund
  idempotency key, so exactly-once and independent terminal confirmation are
  not claimed.
- **Pagination limitation:** The supplied mock supports `limit` but no cursor
  or page token. Reporting requests 250 orders and is therefore bounded by the
  mock limitation; production Shopify cursor pagination remains required.
- **Setup limitation:** The application uses Python standard-library modules
  only. The README documents test and seed-validation commands, but starting
  the supplied mock server is still a separate command and checkout/refund
  are not CLI commands.
- **Generated-file audit:** No local SQLite database, secrets, temporary
  payloads or debug files are included in the project tree. Python cache
  directories were removed after validation.
- **Checkout recovery UI/API:** Persisted uncertain checkouts can be listed and
  inspected, Shopify reconciliation searches only by the original reference,
  known terminal charges can be queried through the existing adapter, and
  operator manual resolution is audited without changing the financial state.

## Stage 1 limitation

Till movements are POS-owned and must be recorded explicitly. Checkout and
refund services do not automatically append cash movements because the
assignment does not define a shared till-session transaction boundary for
those workflows; callers must record `cash_sale` or `cash_refund` using the
completed invoice/refund reference. No Shopify or terminal writes are made by
till operations.
