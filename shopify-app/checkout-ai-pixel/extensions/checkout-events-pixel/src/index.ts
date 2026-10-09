import {register} from "@shopify/web-pixels-extension";

const FUNNEL_EVENTS = new Set([
  "cart_viewed",
  "checkout_started",
  "checkout_contact_info_submitted",
  "checkout_address_info_submitted",
  "checkout_shipping_info_submitted",
  "payment_info_submitted",
  "checkout_completed",
  "alert_displayed",
]);

type JsonRecord = Record<string, unknown>;

function asRecord(value: unknown): JsonRecord {
  return value !== null && typeof value === "object" ? value as JsonRecord : {};
}

function money(value: unknown): {amount?: string; currencyCode?: string} | undefined {
  const record = asRecord(value);
  if (record.amount === undefined && record.currencyCode === undefined) return undefined;
  return {
    amount: record.amount === undefined ? undefined : String(record.amount),
    currencyCode: record.currencyCode === undefined ? undefined : String(record.currencyCode),
  };
}

function checkoutSummary(data: unknown): JsonRecord {
  const checkout = asRecord(asRecord(data).checkout);
  const lineItems = Array.isArray(checkout.lineItems) ? checkout.lineItems : [];

  return {
    token: checkout.token,
    orderId: asRecord(checkout.order).id,
    subtotalPrice: money(checkout.subtotalPrice),
    totalPrice: money(checkout.totalPrice),
    totalTax: money(checkout.totalTax),
    shippingLinePrice: money(asRecord(checkout.shippingLine).price),
    lineItemCount: lineItems.length,
    quantity: lineItems.reduce((total, item) => {
      const quantity = Number(asRecord(item).quantity);
      return total + (Number.isFinite(quantity) ? quantity : 0);
    }, 0),
    discountCodeCount: Array.isArray(checkout.discountApplications)
      ? checkout.discountApplications.length
      : 0,
  };
}

function cartSummary(data: unknown): JsonRecord {
  const cart = asRecord(asRecord(data).cart);
  const lines = Array.isArray(cart.lines) ? cart.lines : [];

  return {
    token: cart.id,
    subtotalPrice: money(asRecord(cart.cost).subtotalAmount),
    totalPrice: money(asRecord(cart.cost).totalAmount),
    lineItemCount: lines.length,
    quantity: lines.reduce((total, line) => {
      const quantity = Number(asRecord(line).quantity);
      return total + (Number.isFinite(quantity) ? quantity : 0);
    }, 0),
  };
}

function alertSummary(data: unknown): JsonRecord | undefined {
  const alert = asRecord(asRecord(data).alert);
  if (Object.keys(alert).length === 0) return undefined;
  return {
    type: alert.type,
    target: alert.target,
    message: alert.message,
  };
}

function checkoutPathToken(context: unknown): string | undefined {
  const location = asRecord(asRecord(asRecord(context).window).location);
  let pathname = String(location.pathname ?? "");
  if (!pathname && location.href) {
    pathname = String(location.href).split("?")[0].replace(/^https?:\/\/[^/]+/, "");
  }
  const parts = pathname.split("/").filter(Boolean);
  const index = parts.indexOf("checkouts");
  if (index < 0 || !parts[index + 1]) return undefined;
  const first = parts[index + 1];
  return first.length <= 3 && parts[index + 2] ? parts[index + 2] : first;
}

register(({analytics, init, settings}) => {
  const endpoint = String(asRecord(settings).collectorEndpoint ?? "");
  if (!endpoint.startsWith("https://")) return;

  analytics.subscribe("all_standard_events", (event) => {
    if (!FUNNEL_EVENTS.has(event.name)) return;

    const context = asRecord(event.context);
    const navigator = asRecord(context.navigator);
    const payload = {
      schemaVersion: 1,
      eventId: event.id,
      eventName: event.name,
      timestamp: event.timestamp,
      clientId: event.clientId,
      shopDomain: asRecord(asRecord(asRecord(init).data).shop).myshopifyDomain,
      userAgent: navigator.userAgent,
      checkoutPathToken: checkoutPathToken(context),
      checkout: event.name === "cart_viewed"
        ? cartSummary(event.data)
        : checkoutSummary(event.data),
      // Never forward the alert's `value`: it can contain buyer-entered data.
      alert: event.name === "alert_displayed" ? alertSummary(event.data) : undefined,
    };

    void fetch(endpoint, {
      method: "POST",
      headers: {"content-type": "application/json"},
      body: JSON.stringify(payload),
    }).catch(() => undefined);
  });
});
