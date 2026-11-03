# Proposed v1 approach and clarifications

Thanks for the requirements. I’ll prioritise a reliable Must-have billing flow and keep Shopify as the system of record for customers, products, inventory, orders, payments and refunds. The POS will own checkout orchestration, per-store invoice numbering, offer evaluation, GST calculation and day-end reporting.

## Implementation approach

The first version will cover:

- Customer lookup and latest valid prescription retrieval.
- Frame and lens selection with prescription-range validation.
- Data-driven offer eligibility and discount calculation.
- GST-inclusive, per-line tax calculation by HSN, using integer paise internally.
- Terminal payment followed by Shopify order creation with invoice reference, line-level tax, discounts and prescription metadata.
- Full refunds with the frame returned to the original billing store and lenses marked as non-restockable.
- Store/date reporting based on Shopify orders, including bills, gross, taxable value, tax, discounts, refunds and payment-method split.

I’ll use a modular application with separate Shopify, prescription and payment adapters, plus durable local records for invoice numbering, checkout state and recovery from uncertain API outcomes.

## Decisions and assumptions

- **Offers:** The supplied offer data will control eligibility, validity, scope and stackability. I’ll use a deterministic evaluation order and avoid combining offers marked non-stackable. The conflict between the named campaign combinations and the supplied flags needs confirmation.
- **Prescriptions:** Lens values will be validated against the selected product’s limits. External prescription values will be preserved exactly; non-standard axes will be flagged for optometrist review rather than rounded.
- **Family billing:** One billing customer and Shopify order can contain multiple lens lines, each linked to its own prescription and family-member reference.
- **Manager discounts:** Store managers can apply up to 10% without a separate approval chain, as requested. Manager identity, reason, percentage and timestamp will be retained for audit.
- **Checkout recovery:** A paid transaction with an uncertain Shopify order result will be reconciled using the persisted checkout and invoice references before any retry. Invoice numbers will not be reused.
- **Refund recovery:** Refund operations will have durable states so partial failures between the terminal and Shopify can be identified and reconciled rather than blindly retried.

## Clarifications needed

1. **Offer stacking — Rohan and Meena:** Should the supplied `stackable` flag always take precedence, even where the named campaign examples suggest that multiple offers should combine?
2. **Manager discount audit — Finance:** Please confirm the required audit fields for an immediate manager discount. I propose recording manager identity, reason code, percentage, timestamp and invoice reference.
3. **External prescriptions — Dr. Iyer:** Is optometrist confirmation the correct handling for external prescriptions with axes outside the in-store 5-degree entry grid?
4. **Expired prescriptions — Dr. Iyer:** Should an expired prescription always block lens checkout, or can an authorised optometrist explicitly revalidate it?
5. **Family billing — Meena and Finance:** Is one Shopify billing customer with separate `rx_id` and family-member references on each lens line sufficient, or must every family member have a separate Shopify customer record?
6. **Invoice recovery — Finance:** If payment succeeds but Shopify order creation cannot be confirmed, is retaining the invoice number as pending reconciliation, then cancelling it if unrecoverable, the accepted treatment? The number cannot safely be reused, and exactly-once remote creation cannot be guaranteed without provider-side idempotency.
7. **Refund recovery — Finance and Operations:** If the terminal refund succeeds but the Shopify refund fails, who owns reconciliation and what is the required escalation or customer communication? The two remote systems cannot be updated atomically.
8. **Remakes — Dr. Iyer, Finance and Operations:** Should a remake create a new lens-only Shopify order linked with `parent_invoice_no`, while leaving the original frame line and inventory unchanged? Should that order be zero-value, or should the applicable charge be determined case by case?
9. **Checkout timing — Rohan:** Does the under-10-second target cover the entire customer and prescription lookup process, or only the final checkout action?
10. **Daily reporting — Rohan and Finance:** The required 10 a.m. report will contain yesterday’s bills by store, average bill value and discounts per store. Is that the complete v1 report content? The POS can generate the report; email scheduling and delivery are production integrations outside the supplied mock.

I’ll proceed with the safe assumptions above unless a clarification changes the required behaviour.

## Scope and delivery

I’ll complete the Must-have flow before taking on till-session management, partial refunds, printable invoices or other Should/Stretch items. The supplied mock interfaces do not provide scheduled email delivery, so I’ll implement the day-end report generation and document email delivery as a production integration.

The beverage queue question, footfall camera issue and changes to lab or eye-test workflows will remain outside the POS scope.
