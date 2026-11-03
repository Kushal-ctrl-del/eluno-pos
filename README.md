# Eluno POS

The Must-tier flows are implemented as a Python standard-library modular
monolith. The core includes customer and prescription validation, cart offers,
GST, payment/checkout orchestration, full refunds, and read-only Shopify-backed
day-end reporting.

Run the tests and seed validation with:

```powershell
& 'C:\Program Files\Python313\python.exe' -m unittest discover -s tests -v
& 'C:\Program Files\Python313\python.exe' starter-kit\validate_seed.py
```

## Local POS frontend

Start the supplied mock services first:

```powershell
& 'C:\Program Files\Python313\python.exe' starter-kit\mock-apis\mock_server.py --no-flake --port 8787
```

In another terminal, start the dependency-free POS web application:

```powershell
& 'C:\Program Files\Python313\python.exe' -m app.entrypoints.web --host 127.0.0.1 --port 8080
```

Open <http://127.0.0.1:8080>. The browser shell reads customers, prescriptions,
products, orders and day-end reports through the local API and delegates
business decisions to the existing application/adapters. No frontend package
manager or third-party dependency is required.

### New-sale demo

1. Open the frontend and search for `+919000000005`.
2. Select a frame, switch to **Lenses**, and select a compatible lens. The
   server validates the lens against prescription `RX5007`; incompatible lens
   selections are rejected before checkout.
3. Review the server-calculated offer, taxable value, CGST, SGST and total.
4. Choose a payment method and submit **Continue to checkout**. The terminal
   is charged by `CheckoutService`, then the Shopify order is created. The UI
   only shows success when the backend returns a captured payment and completed
   Shopify order, and displays the persisted invoice reference.

The workflow API is `POST /api/quote` and `POST /api/checkout`; both accept the
phone and cart line selections and rebuild the domain cart on the server.
The checkout endpoint preserves the existing stable checkout reference,
terminal retry, invoice allocation and Shopify recovery protections. Refund
and till operations are available through the Orders and Till screens:
`GET /api/orders`, `GET /api/order`, `POST /api/refund`,
`POST /api/refund/reconcile`, `GET /api/invoice`, `POST /api/till/open`,
`POST /api/till/movement`, `POST /api/till/close`, `GET /api/till/session`,
and `GET /api/audit`. Refund requests use the existing durable refund ID and
never blindly retry an uncertain financial write. Invoice output is the
existing Shopify-backed plain-text invoice and can be printed from the browser.

Checkout recovery is available from the **Checkout recovery** screen and these
read/recovery routes: `GET /api/checkout/recovery`,
`GET /api/checkout/status?id=...`, `POST /api/checkout/reconcile`,
`POST /api/checkout/terminal-reconcile`, and
`POST /api/checkout/manual-resolution`. Reconciliation only searches Shopify
by the original checkout reference or verifies a known terminal charge through
the terminal adapter; it never creates another charge or order. Manual
resolution records the operator, reason and audit event while leaving the
uncertain financial state unchanged. After a mock-server restart, independent
terminal confirmation may be unavailable and the case remains uncertain.

## Day-end reporting

`ReportingService.day_end(store_code, business_date)` reads Shopify orders for
the selected business date and billing store. It reports bills, gross sales,
taxable value, CGST, SGST, total tax, discounts, refunds, and successful sale
payments split by payment method. Cancelled and voided orders are excluded.
Refunds are attributed to the original sale's date and billing store; the
assignment does not specify a refund-date convention, so this is the v1
assumption.

The supplied mock order collection supports a `limit` but no cursor or page
token. The adapter requests 250 records, which is sufficient for the fixture,
but production must implement Shopify cursor pagination before relying on this
report for an unbounded store.

## External-operation limitation

If a terminal refund times out, the mock API provides no refund-status lookup or
idempotency key. A manual terminal-refund resolution is therefore an operator
assertion supported by recorded evidence, not independent confirmation from the
terminal. The application refuses blind retries and cannot claim exactly-once
or automatic verification for that uncertain operation.

## Till sessions

Till sessions are POS-owned SQLite records. Use the CLI to open a session,
record cash movements, and close it with a counted drawer:

```powershell
python -m app.entrypoints.cli till-open --store JN --location-id 70000005 --staff S120 --float-paise 300000 --session-id TILL-JN-1
python -m app.entrypoints.cli till-movement --session-id TILL-JN-1 --type cash_sale --amount-paise 899800 --reference ELU/JN/FY26/00001
python -m app.entrypoints.cli till-movement --session-id TILL-JN-1 --type paid_out --amount-paise 12000 --reference PO-JN-0017 --reason courier
python -m app.entrypoints.cli till-close --session-id TILL-JN-1 --staff S120 --counted-paise 1187800
```

Cash sales and paid-ins increase the expected drawer; cash refunds and
paid-outs decrease it. Cash refunds, paid-ins, and paid-outs require a reason.
A variance greater than ₹200 in either direction is flagged at close.

## Partial refunds

`RefundService.refund_partial()` accepts selected Shopify line item IDs and
positive quantities. Shopify's refund calculation endpoint is called before
the terminal or Shopify refund write. The calculated amount and tax reversal
are then used for both financial writes. Frame selections use
`restock_type=return` at the original order location; lens selections always
use `no_restock`.

The same durable refund states and duplicate protections as full refunds are
used. A selected line cannot exceed its remaining refundable quantity, and a
single request cannot select a line twice. The terminal mock only supports an
amount-based refund with no idempotency key or refund-status lookup, so
uncertain terminal partial refunds have the same manual-resolution limitation
as full refunds.

## Printable invoices

Render a deterministic plain-text invoice from a completed Shopify order:

```powershell
python -m app.entrypoints.cli invoice --order-id 99000005
```

The renderer reads financial values, tax lines, discounts, transactions,
prescription properties and refund details from Shopify, and reads store
identity/address from the read-only locations endpoint. It does not recalculate
checkout values or write to Shopify. This follows
`starter-kit/sample_invoice.txt`, including HSN, GST rate labels, amount in
words, and the sample lens/return note. PDF generation, printer hardware and
e-invoicing/IRN are outside the supplied mock contract.

## Inventory and discount audit details

Checkout orders use Shopify's documented
`inventory_behaviour=decrement_obeying_policy`, so tracked frame variants are
decremented at the billing location; lenses without inventory items are not
decremented. HSN is persisted on each line as an `hsn` line property because
the mock has no native HSN line field. Manager discounts carry the entered
reason code into the application audit event rather than replacing it with a
generic label.

## Sales channels

Checkout accepts the assignment's supported channels only:
`walk_in`, `whatsapp_booked`, and `online_pickup`. The selected value is
written as the Shopify `channel` note attribute alongside the existing invoice,
store, staff, payment and checkout-reference attributes.

The day-end report retains its existing store/date, tax, discount, refund and
payment-method totals and adds `sales_channel_paise`, with the CLI exposing it
as `sales_channel_split`. Orders missing the channel attribute, or carrying a
legacy value outside the assignment vocabulary, are reported under an
`unknown` bucket. They are not reclassified or accepted for new checkout
requests.

## Audit trail

Application-owned audit events are stored append-only in the SQLite
`audit_events` table. They capture the staff actor when available, action,
entity type/reference, timestamp, required reason, and limited structured
metadata such as amounts, statuses, invoice/order/refund references, and
offer IDs. Customer contact data, payment credentials, and full external
payloads are not recorded.

Events cover till opening, movements and closing; checkout start, payment
failure/capture/recovery, Shopify uncertainty and completion; refund
initiation, terminal/Shopify outcomes, reconciliation and manual terminal
resolution; and manager discretionary discounts. Audit records describe the
application-observed outcome only and are not independent confirmation of an
uncertain terminal or Shopify operation. The application exposes no audit
update or delete operation.
