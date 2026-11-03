const state = { products: [], cart: [], productType: "Frame", customer: null, quote: null, busy: false, tillSession: null, checkoutBlocked: false };
const money = paise => `₹${(Number(paise || 0) / 100).toLocaleString("en-IN", {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
const $ = selector => document.querySelector(selector);

async function api(path) {
  const response = await fetch(path);
  const body = await response.json();
  if (!response.ok) { const error = new Error(body.error?.message || "The POS service returned an error."); error.code = body.error?.code; error.status = response.status; throw error; }
  return body;
}

function alertError(error) {
  $("#alerts").innerHTML = `<div class="alert" role="alert">${error.message}</div>`;
  window.setTimeout(() => { $("#alerts").innerHTML = ""; }, 5000);
}

function renderProducts() {
  const search = ($("#catalog-search")?.value || "").toLowerCase();
  const products = state.products.filter(product => product.product_type === state.productType &&
    `${product.name} ${product.sku} ${product.variant_title}`.toLowerCase().includes(search));
  $("#catalog").innerHTML = products.map(product =>
    `<button class="product-card" data-variant="${product.variant_id}" data-title="${escapeHtml(product.name)}" data-price="${product.price}" data-type="${product.attributes.line_type}" data-sku="${escapeHtml(product.sku)}">
      <h3>${escapeHtml(product.name)}</h3><p>${escapeHtml(product.variant_title || product.sku)} · ${escapeHtml(product.sku)}</p><span class="price">${money(Math.round(Number(product.price) * 100))}</span>
      <small>${escapeHtml(product.attributes.material || product.attributes.lens_type || "")} ${escapeHtml(product.attributes.coating || "")}</small>
    </button>`).join("") || `<div class="empty-state">No products available from Shopify.</div>`;
  document.querySelectorAll(".product-card").forEach(button => button.addEventListener("click", () => addToCart(button.dataset)));
}

function addToCart(data) {
  if (data.type === "lens" && !state.customer) {
    alertError(new Error("Search for a customer and load a valid prescription before selecting lenses."));
    return;
  }
  if (state.cart.some(line => line.variant_id === Number(data.variant))) return;
  const pairRef = data.type === "lens" ? "p1" : `p${state.cart.filter(line => line.type === "frame").length + 1}`;
  state.cart.push({line_id: `line-${state.cart.length + 1}`, variant_id: Number(data.variant), title: data.title, price_paise: Math.round(Number(data.price) * 100), type: data.type, pair_ref: pairRef, sku: data.sku});
  renderCart();
  refreshQuote();
}

function renderCart() {
  const subtotal = state.cart.reduce((sum, line) => sum + line.price_paise, 0);
  $("#cart-count").textContent = state.cart.length;
  $("#subtotal").textContent = money(subtotal);
  $("#total").textContent = money(subtotal);
  $("#checkout-button").disabled = state.cart.length === 0 || !state.quote || !state.tillSession || state.checkoutBlocked;
  const taxByLine = Object.fromEntries((state.quote?.tax?.lines || []).map(line => [line.line_id, line]));
  const allocations = {};
  (state.quote?.offers?.applied || []).forEach(offer => offer.allocations.forEach(item => {
    allocations[item.line_id] = (allocations[item.line_id] || 0) + item.amount_paise;
  }));
  $("#cart-lines").innerHTML = state.cart.length ? state.cart.map(line => {
    const tax = taxByLine[line.line_id];
    const discount = allocations[line.line_id] || 0;
    const detail = tax ? `<small>Taxable ${money(tax.taxable_paise)} · CGST ${money(tax.cgst_paise)} · SGST ${money(tax.sgst_paise)}${discount ? ` · Discount ${money(discount)}` : ""}</small>` : "";
    return `<div class="cart-line"><div><strong>${escapeHtml(line.title)}</strong><small>${line.type} · ${escapeHtml(line.sku || "")}</small>${detail}</div><button class="remove-line" data-line="${line.line_id}" aria-label="Remove ${escapeHtml(line.title)}">×</button><strong>${money(line.price_paise)}</strong></div>`;
  }).join("") :
    `<div class="empty-state">Your cart is empty.<br><span>Select a frame to begin.</span></div>`;
  document.querySelectorAll(".remove-line").forEach(button => button.addEventListener("click", () => {
    state.cart = state.cart.filter(line => line.line_id !== button.dataset.line); renderCart(); refreshQuote();
  }));
}

function renderCustomer(data) {
  const customer = data.customer, rx = data.prescription;
  state.customer = data;
  $("#customer-status").textContent = "Customer found";
  $("#customer-result").innerHTML = `<div class="customer-card"><div><strong>${escapeHtml(customer.phone)}</strong><small>Customer #${customer.id} · ${customer.tags.join(", ") || "No tags"}</small></div><div class="rx-badge">RX ${escapeHtml(rx.rx_id)} · valid until ${escapeHtml(rx.valid_until)}</div></div>`;
}

async function refreshQuote() {
  if (!state.customer || !state.cart.length) {
    state.quote = null;
    $("#discounts").textContent = "—";
    $("#taxable").textContent = "—";
    $("#gst-split").textContent = "—";
    $("#checkout-button").disabled = true;
    renderCart();
    return;
  }
  try {
    const body = {phone: $("#phone").value, lines: state.cart.map(line => ({line_id: line.line_id, variant_id: line.variant_id, line_type: line.type, pair_ref: line.pair_ref}))};
    state.quote = await postApi("/api/quote", body);
    $("#discounts").textContent = money(state.quote.offers.total_discount_paise);
    $("#taxable").textContent = money(state.quote.tax.taxable_paise);
    $("#gst-split").textContent = `${money(state.quote.tax.cgst_paise)} · ${money(state.quote.tax.sgst_paise)}`;
    $("#total").textContent = money(state.quote.tax.total_paise);
    renderCart();
    $("#checkout-button").disabled = !state.tillSession || state.checkoutBlocked;
  } catch (error) {
    state.quote = null; $("#checkout-button").disabled = true; renderCart(); alertError(error);
  }
}

async function postApi(path, body) {
  const response = await fetch(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  const result = await response.json();
  if (!response.ok) { const error = new Error(result.error?.message || "The POS service returned an error."); error.code = result.error?.code; error.status = response.status; throw error; }
  return result;
}

function renderReport(report) {
  const metrics = [["Bills", report.bill_count],["Gross sales", money(report.gross_sales_paise)],["Taxable value", money(report.taxable_value_paise)],["Total tax", money(report.total_tax_paise)],["Discounts", money(report.discounts_paise)],["Refunds", money(report.refund_amount_paise)],["CGST", money(report.cgst_paise)],["SGST", money(report.sgst_paise)]];
  $("#report-result").innerHTML = metrics.map(([label, value]) => `<div class="metric"><small>${label}</small><strong>${value}</strong></div>`).join("");
  const till = report.till;
  $("#report-detail").innerHTML = `<strong>Payment methods</strong> ${Object.entries(report.payment_method_paise || {}).map(([key, value]) => `${escapeHtml(key)} ${money(value)}`).join(" · ") || "No reconciled payments"}<br><strong>Channels</strong> ${Object.entries(report.sales_channel_paise || {}).map(([key, value]) => `${escapeHtml(key)} ${money(value)}`).join(" · ") || "No channel data"}<br><strong>Till</strong> ${till ? `opening ${money(till.opening_float_paise)} · expected ${till.expected_cash_paise == null ? "—" : money(till.expected_cash_paise)} · variance ${till.variance_paise == null ? "—" : money(till.variance_paise)}` : "No till session recorded for this store."}`;
}

async function loadOrders() {
  const data = await api(`/api/orders?q=${encodeURIComponent($("#order-search").value)}`);
  $("#orders-list").innerHTML = data.orders.length ? data.orders.map(order => {
    const invoice = (order.note_attributes || []).find(item => item.name === "invoice_no")?.value || "No invoice";
    return `<div class="order-row"><div><strong>${escapeHtml(order.name || `Order ${order.id}`)}</strong><small>${escapeHtml(invoice)} · ${escapeHtml(order.created_at || "")}</small></div><button class="button button-dark order-open" data-id="${order.id}">Open</button></div>`;
  }).join("") : `<div class="empty-state">No Shopify orders matched the search.</div>`;
  document.querySelectorAll(".order-open").forEach(button => button.addEventListener("click", () => loadOrder(button.dataset.id)));
}

async function loadOrder(id) {
  try {
    const data = await api(`/api/order?id=${id}`), order = data.order;
    const lines = order.line_items || [];
    $("#order-result").innerHTML = `<div class="order-detail"><h3>${escapeHtml(order.name || `Order ${order.id}`)}</h3><p class="muted">${escapeHtml(order.customer?.phone || "Customer unavailable")} · ${escapeHtml(order.financial_status || "status unavailable")}</p><table class="line-table">${lines.map(line => `<tr><td><input class="refund-line" type="checkbox" data-line-id="${line.id}" data-quantity="${line.quantity}"> <strong>${escapeHtml(line.title || "Item")}</strong><br><small>${line.quantity} × ${escapeHtml(line.price)}</small></td><td>${escapeHtml(line.total_discount || "0.00")} discount</td></tr>`).join("")}</table><p><strong>Total ₹${escapeHtml(order.total_price)}</strong></p><div class="refund-box"><input id="refund-reason" placeholder="Refund reason" value="customer_request"><button class="button button-dark" id="partial-refund" data-order="${order.id}">Refund selected</button><button class="button button-dark" id="full-refund" data-order="${order.id}">Refund remaining items</button><button class="button button-primary" id="invoice-open" data-order="${order.id}">View / print invoice</button><div id="refund-status" class="muted"></div></div></div>`;
    $("#full-refund").addEventListener("click", () => submitRefund(order.id, null));
    $("#partial-refund").addEventListener("click", () => {
      const selected = [...document.querySelectorAll(".refund-line:checked")].map(input => ({line_item_id: Number(input.dataset.lineId), quantity: Number(input.dataset.quantity)}));
      if (!selected.length) { $("#refund-status").textContent = "Select at least one refundable line."; return; }
      submitRefund(order.id, selected);
    });
    $("#invoice-open").addEventListener("click", () => window.open(`/api/invoice?id=${order.id}`, "_blank"));
  } catch (error) { alertError(error); }
}

async function submitRefund(orderId, selectedLines) {
  const button = $("#full-refund");
  if (button) button.disabled = true;
  try {
    const result = await postApi("/api/refund", {refund_request_id: `web-refund-${Date.now()}`, order_id: Number(orderId), reason: $("#refund-reason").value, selected_lines: selectedLines});
    $("#refund-status").textContent = `Refund ${result.refund.status}: ₹${(result.refund.amount_paise / 100).toFixed(2)}`;
  } catch (error) { $("#refund-status").textContent = error.message; alertError(error); if (button) button.disabled = false; }
}

async function loadTill() {
  try {
    const active = await api("/api/till/open");
    if (!active.session) {
      state.tillSession = null;
      $("#till-status").textContent = "Till not open";
      $("#till-session").textContent = "No open till session.";
      $("#checkout-button").disabled = true;
      return;
    }
    const data = await api(`/api/till/session?id=${encodeURIComponent(active.session)}`);
    const session = data.session;
    state.tillSession = session;
    $("#till-status").textContent = "Till ready";
    if (state.quote) $("#checkout-button").disabled = false;
    $("#till-session").innerHTML = `<strong>${escapeHtml(session.session_id)}</strong><p>Opening float ${money(session.opening_float_paise)}</p><p>Expected cash ${session.expected_cash_paise == null ? "—" : money(session.expected_cash_paise)}</p><div class="form-stack"><input id="movement-amount" type="number" placeholder="Movement amount (paise)"><input id="movement-reason" placeholder="Reason required"><button class="button button-dark" id="paid-out">Record paid out</button><input id="counted-cash" type="number" placeholder="Counted cash (paise)"><button class="button button-primary" id="close-till">Close till</button></div>`;
    $("#paid-out").addEventListener("click", async () => { await postApi("/api/till/movement", {session_id: session.session_id, movement_type: "paid_out", amount_paise: Number($("#movement-amount").value), reference: `web-${Date.now()}`, reason: $("#movement-reason").value}); await loadTill(); });
    $("#close-till").addEventListener("click", async () => { const result = await postApi("/api/till/close", {session_id: session.session_id, counted_cash_paise: Number($("#counted-cash").value), staff_id: "S105"}); $("#till-session").textContent = `Closed. Variance ${money(result.session.variance_paise)}${result.session.variance_flagged ? " · flagged" : ""}`; });
  } catch (error) { alertError(error); }
}

async function loadAudit() {
  try {
    const data = await api("/api/audit");
    $("#audit-list").innerHTML = `<table class="activity-table"><thead><tr><th>Time</th><th>Operator</th><th>Action</th><th>Reference</th><th>Reason</th></tr></thead><tbody>${data.events.slice().reverse().map(event => `<tr><td>${escapeHtml(event.occurred_at)}</td><td>${escapeHtml(event.actor_id || "system")}</td><td>${escapeHtml(event.action)}</td><td>${escapeHtml(event.entity_ref)}</td><td>${escapeHtml(event.reason || "—")}</td></tr>`).join("")}</tbody></table>` || "No activity recorded.";
  } catch (error) { alertError(error); }
}

function recoveryLabel(checkout) {
  if (checkout.status === "payment_uncertain") return "Terminal payment uncertain";
  if (checkout.status === "shopify_reconciliation_required") return "Payment captured · Shopify order unconfirmed";
  return checkout.status;
}

async function loadRecovery() {
  try {
    const data = await api("/api/checkout/recovery");
    $("#recovery-list").innerHTML = data.checkouts.length ? data.checkouts.map(checkout =>
      `<button class="order-row recovery-open" data-id="${escapeHtml(checkout.checkout_id)}"><span><strong>${escapeHtml(checkout.checkout_id)}</strong><small>${escapeHtml(checkout.invoice_number || "No invoice")} · ${escapeHtml(recoveryLabel(checkout))}</small></span><span>${escapeHtml(checkout.terminal_charge_id || "No charge")}</span></button>`
    ).join("") : `<div class="empty-state">No uncertain checkouts require recovery.</div>`;
    document.querySelectorAll(".recovery-open").forEach(button => button.addEventListener("click", () => loadRecoveryDetail(button.dataset.id)));
  } catch (error) { alertError(error); }
}

async function loadRecoveryDetail(checkoutId) {
  try {
    const data = await api(`/api/checkout/status?id=${encodeURIComponent(checkoutId)}`);
    const checkout = data.checkout;
    const warning = checkout.status === "shopify_reconciliation_required"
      ? `<div class="alert">Payment is captured, but the Shopify order is unconfirmed. Do not retry checkout.</div>` : "";
    const resolution = checkout.resolution_id
      ? `<p class="muted">Manual resolution recorded by ${escapeHtml(checkout.resolution_actor_id)}: ${escapeHtml(checkout.resolution_reason)}</p>`
      : `<div class="form-stack"><input id="recovery-actor" value="S105" placeholder="Operator ID"><input id="recovery-reason" placeholder="Manual resolution reason"><button class="button button-dark" id="manual-resolution">Record manual resolution</button></div>`;
    const reconcile = checkout.status === "shopify_reconciliation_required"
      ? `<button class="button button-dark" id="reconcile-shopify">Search Shopify by original reference</button>` : "";
    const terminal = checkout.status === "payment_uncertain" && checkout.terminal_charge_id
      ? `<button class="button button-dark" id="reconcile-terminal">Verify known terminal charge</button>` : "";
    $("#recovery-detail").innerHTML = `<div class="order-detail"><h3>${escapeHtml(checkout.checkout_id)}</h3>${warning}<p><strong>Invoice:</strong> ${escapeHtml(checkout.invoice_number || "—")}<br><strong>Terminal charge:</strong> ${escapeHtml(checkout.terminal_charge_id || "—")}<br><strong>Payment status:</strong> ${escapeHtml(checkout.status === "shopify_reconciliation_required" ? "captured" : checkout.status)}<br><strong>Shopify order:</strong> ${escapeHtml(checkout.shopify_order_id || "unconfirmed")}<br><strong>Last reconciliation:</strong> ${escapeHtml(checkout.last_reconciliation_result || "Not attempted")}<br><strong>Last result detail:</strong> ${escapeHtml(checkout.last_error || "—")}<br><strong>Last updated:</strong> ${escapeHtml(checkout.updated_at || "—")}</p>${reconcile}${terminal}${resolution}<div id="recovery-status" class="muted"></div></div>`;
    $("#reconcile-shopify")?.addEventListener("click", async () => {
      try { const result = await postApi("/api/checkout/reconcile", {checkout_id: checkout.checkout_id}); $("#recovery-status").textContent = `Reconciliation: ${result.checkout.status} · Shopify order ${result.checkout.shopify_order_id || "not found"}`; await loadRecovery(); await loadRecoveryDetail(checkout.checkout_id); }
      catch (error) { $("#recovery-status").textContent = error.message; alertError(error); }
    });
    $("#reconcile-terminal")?.addEventListener("click", async () => {
      try { const result = await postApi("/api/checkout/terminal-reconcile", {checkout_id: checkout.checkout_id, charge_id: checkout.terminal_charge_id}); $("#recovery-status").textContent = `Terminal status: ${result.status}`; await loadRecoveryDetail(checkout.checkout_id); }
      catch (error) { $("#recovery-status").textContent = error.message; alertError(error); }
    });
    $("#manual-resolution")?.addEventListener("click", async () => {
      try { await postApi("/api/checkout/manual-resolution", {checkout_id: checkout.checkout_id, actor_id: $("#recovery-actor").value, reason: $("#recovery-reason").value}); await loadRecovery(); await loadRecoveryDetail(checkout.checkout_id); }
      catch (error) { $("#recovery-status").textContent = error.message; alertError(error); }
    });
  } catch (error) { alertError(error); }
}

function escapeHtml(value) { return String(value).replace(/[&<>"']/g, char => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;" }[char])); }

document.querySelectorAll(".nav-item").forEach(item => item.addEventListener("click", () => {
  document.querySelectorAll(".nav-item").forEach(node => node.classList.toggle("active", node === item));
  document.querySelectorAll(".page").forEach(page => page.classList.toggle("active", page.id === `${item.dataset.page}-page`));
  $("#page-title").textContent = item.textContent.trim();
}));

document.querySelectorAll(".segment").forEach(item => item.addEventListener("click", () => {
  document.querySelectorAll(".segment").forEach(node => node.classList.toggle("active", node === item));
  state.productType = item.dataset.productType;
  renderProducts();
}));

$("#customer-form").addEventListener("submit", async event => {
  event.preventDefault();
  $("#customer-status").textContent = "Searching…";
  try { renderCustomer(await api(`/api/customer?phone=${encodeURIComponent($("#phone").value)}`)); await refreshQuote(); }
  catch (error) { $("#customer-status").textContent = "Search failed"; $("#customer-result").textContent = error.message; alertError(error); }
});

$("#catalog-search").addEventListener("input", renderProducts);
$("#checkout-button").addEventListener("click", async () => {
  if (!state.quote || state.busy || state.checkoutBlocked) return;
  state.busy = true; $("#checkout-button").disabled = true; $("#checkout-button").innerHTML = "Payment pending…";
  try {
    const result = await postApi("/api/checkout", {
      phone: $("#phone").value,
      lines: state.cart.map(line => ({line_id: line.line_id, variant_id: line.variant_id, line_type: line.type, pair_ref: line.pair_ref})),
      checkout_id: `web-${Date.now()}`, payment_method: $("#payment-method").value,
      sales_channel: $("#sales-channel").value, store_code: "HSR", location_id: 70000002,
      staff_id: "S105", invoice_prefix: "ELU/HSR/FY26/",
      active_till: Boolean(state.tillSession),
      session_id: state.tillSession?.session_id || null
    });
    $("#alerts").innerHTML = `<div class="alert success">Payment captured · invoice ${escapeHtml(result.invoice_number)} · Shopify order ${escapeHtml(result.shopify_order_id || "pending")}</div>`;
    state.cart = []; state.quote = null; state.checkoutBlocked = false; renderCart();
  } catch (error) {
    if (["payment_uncertain", "shopify_uncertain", "checkout_in_progress"].includes(error.code)) {
      state.checkoutBlocked = true;
      alertError(new Error(`${error.message} Open Checkout recovery; do not retry this sale.`));
    } else {
      alertError(error);
    }
  }
  finally { state.busy = false; $("#checkout-button").innerHTML = "Continue to checkout <span>→</span>"; $("#checkout-button").disabled = !state.quote || !state.tillSession || state.checkoutBlocked; }
});

$("#order-search-form").addEventListener("submit", async event => { event.preventDefault(); try { await loadOrders(); } catch (error) { alertError(error); } });
$("#till-open-form").addEventListener("submit", async event => { event.preventDefault(); try { await postApi("/api/till/open", {store_code: "HSR", location_id: 70000002, staff_id: "S105", opening_float_paise: Number($("#till-float").value)}); await loadTill(); } catch (error) { alertError(error); } });
document.querySelector('[data-page="orders"]').addEventListener("click", loadOrders);
document.querySelector('[data-page="till"]').addEventListener("click", loadTill);
document.querySelector('[data-page="audit"]').addEventListener("click", loadAudit);
document.querySelector('[data-page="recovery"]').addEventListener("click", loadRecovery);

$("#report-form").addEventListener("submit", async event => {
  event.preventDefault();
  try { renderReport(await api(`/api/report?store=${encodeURIComponent($("#report-store").value)}&date=${encodeURIComponent($("#report-date").value)}`)); }
  catch (error) { $("#report-result").innerHTML = `<div class="panel empty-state">${escapeHtml(error.message)}</div>`; alertError(error); }
});

async function initialise() {
  try {
    const data = await api("/api/products?type=Frame");
    state.products = data.products;
    renderProducts();
    const lensData = await api("/api/products?type=Lens");
    state.products = state.products.concat(lensData.products);
  } catch (error) { $("#catalog").innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`; alertError(error); }
  renderCart();
  $("#report-date").value = new Date().toISOString().slice(0, 10);
  await loadTill();
}
initialise();
