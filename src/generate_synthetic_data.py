"""Generate privacy-safe fixtures shaped like Shopify Admin and Web Pixel data."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path


FUNNEL = [
    "cart_viewed",
    "checkout_started",
    "checkout_contact_info_submitted",
    "checkout_address_info_submitted",
    "checkout_shipping_info_submitted",
    "payment_info_submitted",
    "checkout_completed",
]

SCENARIOS = {
    "cart_not_checkout": (0, None, "REVIEW_CART_VALUE"),
    "drop_after_checkout_start": (1, None, "REVIEW_CHECKOUT_ENTRY"),
    "drop_after_contact": (2, None, "SEND_CHECKOUT_REMINDER"),
    "drop_after_address": (3, None, "CHECK_DELIVERY_SETTINGS"),
    "drop_after_shipping": (4, None, "SEND_CHECKOUT_REMINDER"),
    "high_shipping_ratio": (4, None, "OFFER_FREE_SHIPPING"),
    "high_extra_costs": (4, None, "REVIEW_EXTRA_COSTS"),
    "payment_error": (5, "PAYMENT_ERROR", "SUGGEST_ALTERNATIVE_PAYMENT"),
    "discount_error": (3, "DISCOUNT_ERROR", "CHECK_DISCOUNT_CODE"),
    "inventory_error": (2, "INVENTORY_ERROR", "CHECK_PRODUCT_STOCK"),
    "high_cart_value": (2, None, "OFFER_PERSONAL_HELP"),
    "repeated_abandonment": (4, None, "AVOID_AUTOMATIC_DISCOUNT"),
    "reminder_responsive": (3, None, "SEND_CHECKOUT_REMINDER"),
    "message_non_responder": (3, None, "STOP_MESSAGES"),
    "completed": (6, None, "NO_ACTION"),
}

COUNTRIES = ["US", "CA", "GB", "DE", "FR", "PL", "UA"]
SOURCES = ["direct", "organic", "paid_search", "paid_social", "email", "referral"]
DEVICES = ["mobile", "mobile", "mobile", "desktop", "desktop", "tablet"]


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def money(amount: float, currency: str = "USD") -> dict:
    return {"amount": round(amount, 2), "currencyCode": currency}


def money_bag(amount: float, currency: str = "USD") -> dict:
    return {"shopMoney": {"amount": f"{amount:.2f}", "currencyCode": currency}}


def split_for(client_id: str) -> str:
    bucket = int(hashlib.sha256(client_id.encode()).hexdigest()[:8], 16) % 100
    return "train" if bucket < 70 else "validation" if bucket < 85 else "test"


def checkout_payload(case: dict) -> dict:
    delivery = None
    if case["last_stage_index"] >= 4:
        delivery = {
            "selectedDeliveryOptions": [{
                "cost": money(case["shipping_price"]),
                "costAfterDiscounts": money(case["shipping_price"]),
                "description": "Synthetic delivery option",
                "handle": f"synthetic-rate-{case['case_number']}",
                "title": "Standard shipping",
                "type": "shipping",
            }]
        }
    return {
        "token": case["checkout_token"],
        "currencyCode": "USD",
        "lineItems": case["pixel_line_items"],
        "subtotalPrice": money(case["subtotal_price"]),
        "totalPrice": money(case["total_price"]),
        "totalTax": money(case["tax_amount"]),
        "discountsAmount": money(case["discount_amount"]),
        "discountApplications": case["discount_applications"],
        "delivery": delivery,
        "order": {"id": f"gid://shopify/Order/{case['case_number']}"}
        if case["scenario"] == "completed" else None,
        "transactions": [],
    }


def event_context(case: dict) -> dict:
    widths = {"mobile": 390, "tablet": 820, "desktop": 1440}
    return {
        "document": {"referrer": f"https://synthetic.example/{case['traffic_source']}"},
        "navigator": {"language": "en", "userAgent": f"Synthetic {case['device_type']} browser"},
        "window": {"innerHeight": 900, "innerWidth": widths[case["device_type"]]},
    }


def make_events(case: dict, start: datetime) -> list[dict]:
    events = []
    current = start
    for seq, name in enumerate(FUNNEL[:case["last_stage_index"] + 1], start=1):
        current += timedelta(minutes=case["rng"].randint(1, 8))
        data = {"cart": {
            "id": case["cart_id"],
            "cost": {"totalAmount": money(case["subtotal_price"])},
            "lines": case["cart_lines"],
            "totalQuantity": case["items_quantity"],
        }} if name == "cart_viewed" else {"checkout": checkout_payload(case)}
        events.append({
            "id": f"synthetic-event-{case['case_number']}-{seq}",
            "name": name,
            "clientId": case["client_id"],
            "timestamp": iso(current),
            "seq": seq,
            "type": "standard",
            "context": event_context(case),
            "data": data,
        })
    if case["alert_type"]:
        current += timedelta(seconds=10)
        events.append({
            "id": f"synthetic-event-{case['case_number']}-alert",
            "name": "alert_displayed",
            "clientId": case["client_id"],
            "timestamp": iso(current),
            "seq": len(events) + 1,
            "type": "standard",
            "context": event_context(case),
            "data": {"alert": {
                "message": f"Synthetic {case['alert_type'].lower()}",
                "target": "cart",
                "type": case["alert_type"],
                "value": None,
            }},
        })
    return events


def make_case(rng: random.Random, index: int, client_number: int, scenario: str) -> dict:
    last_stage_index, alert_type, recommendation = SCENARIOS[scenario]
    items_quantity = rng.randint(1, 6)
    line_items_count = rng.randint(1, min(items_quantity, 4))
    merchandise_total = round(rng.uniform(25, 220), 2)
    if scenario == "high_cart_value":
        merchandise_total = round(rng.uniform(350, 900), 2)
    discount_percent = rng.choice([0, 0, 0, 5, 10])
    discount = round(merchandise_total * discount_percent / 100, 2)
    subtotal = round(merchandise_total - discount, 2)
    shipping = round(rng.uniform(4, 16), 2) if last_stage_index >= 4 else 0.0
    if scenario == "high_shipping_ratio":
        merchandise_total = round(rng.uniform(25, 55), 2)
        discount = round(merchandise_total * discount_percent / 100, 2)
        subtotal = round(merchandise_total - discount, 2)
        shipping = round(rng.uniform(15, 25), 2)
    tax = round((subtotal + shipping) * rng.uniform(0.03, 0.10), 2)
    duties = round(rng.uniform(8, 25), 2) if scenario == "high_extra_costs" else 0.0
    total = round(subtotal + shipping + tax + duties, 2)
    previous_orders = rng.choice([0, 0, 1, 2, 4, 8, 15])
    previous_abandonments = rng.randint(3, 8) if scenario == "repeated_abandonment" else rng.randint(0, 2)
    recovery_emails_sent = 3 if scenario == "message_non_responder" else rng.randint(0, 1)
    previous_reminder_recoveries = rng.randint(1, 4) if scenario == "reminder_responsive" else 0
    client_id = f"synthetic-client-{client_number:06d}"
    checkout_token = f"synthetic-checkout-{index:07d}"
    discount_applications = [] if discount_percent == 0 else [{
        "allocationMethod": "ACROSS", "targetSelection": "ALL",
        "targetType": "LINE_ITEM", "title": f"SYNTHETIC{discount_percent}",
        "type": "DISCOUNT_CODE", "value": {"percentage": discount_percent},
    }]
    product_id = 20_000_000 + index
    pixel_line_items = [{
        "id": f"gid://shopify/CheckoutLineItem/{index}",
        "quantity": items_quantity,
        "title": "Synthetic product",
        "variant": {"id": f"gid://shopify/ProductVariant/{product_id}"},
    }]
    cart_lines = [{
        "quantity": items_quantity,
        "cost": {"totalAmount": money(subtotal)},
        "merchandise": {"id": f"gid://shopify/ProductVariant/{product_id}"},
    }]
    return {
        "rng": rng,
        "case_number": index,
        "case_id": f"case-{index:07d}",
        "client_id": client_id,
        "cart_id": f"gid://shopify/Cart/synthetic-{index:07d}",
        "checkout_token": checkout_token,
        "scenario": scenario,
        "last_stage_index": last_stage_index,
        "alert_type": alert_type,
        "recommended_action": recommendation,
        "country_code": rng.choice(COUNTRIES),
        "device_type": rng.choice(DEVICES),
        "traffic_source": rng.choice(SOURCES),
        "line_items_count": line_items_count,
        "items_quantity": items_quantity,
        "subtotal_price": subtotal,
        "discount_amount": discount,
        "discount_percent": discount_percent,
        "shipping_price": shipping,
        "tax_amount": tax,
        "duties_amount": duties,
        "total_price": total,
        "previous_orders": previous_orders,
        "customer_amount_spent": round(previous_orders * rng.uniform(40, 180), 2),
        "previous_abandonments": previous_abandonments,
        "recovery_emails_sent": recovery_emails_sent,
        "previous_reminder_recoveries": previous_reminder_recoveries,
        "inventory_available": scenario != "inventory_error",
        "discount_applications": discount_applications,
        "pixel_line_items": pixel_line_items,
        "cart_lines": cart_lines,
    }


def make_abandoned_checkout(case: dict, start: datetime, updated: datetime) -> dict | None:
    if case["last_stage_index"] < 2:
        return None
    completed_at = updated if case["scenario"] == "completed" else None
    return {
        "id": f"gid://shopify/AbandonedCheckout/{case['case_number']}",
        "name": f"#{100000 + case['case_number']}",
        "createdAt": iso(start),
        "updatedAt": iso(updated),
        "completedAt": iso(completed_at) if completed_at else None,
        "discountCodes": [f"SYNTHETIC{case['discount_percent']}"] if case["discount_percent"] else [],
        "subtotalPriceSet": money_bag(case["subtotal_price"]),
        "totalDiscountSet": money_bag(case["discount_amount"]),
        "totalDutiesSet": money_bag(case["duties_amount"]),
        "totalLineItemsPriceSet": money_bag(case["subtotal_price"] + case["discount_amount"]),
        "totalPriceSet": money_bag(case["total_price"]),
        "totalTaxSet": money_bag(case["tax_amount"]),
        "taxesIncluded": False,
        "shippingAddress": {"countryCodeV2": case["country_code"]}
        if case["last_stage_index"] >= 3 else None,
        "customer": {
            "id": f"gid://shopify/Customer/{case['client_id'].split('-')[-1]}",
            "numberOfOrders": str(case["previous_orders"]),
            "amountSpent": money(case["customer_amount_spent"]),
        },
        "lineItems": {"nodes": [{
            "id": f"gid://shopify/AbandonedCheckoutLineItem/{case['case_number']}",
            "quantity": case["items_quantity"],
            "title": "Synthetic product",
            "sku": f"SYN-{case['case_number']:07d}",
            "originalTotalPriceSet": money_bag(case["subtotal_price"] + case["discount_amount"]),
            "discountedTotalPriceSet": money_bag(case["subtotal_price"]),
        }]},
    }


def make_abandonment(case: dict, checkout: dict | None, start: datetime) -> dict:
    email_state = "SENT" if case["recovery_emails_sent"] else "NOT_SENT"
    return {
        "id": f"gid://shopify/Abandonment/{case['case_number']}",
        "abandonmentType": "CHECKOUT" if case["last_stage_index"] >= 1 else "CART",
        "mostRecentStep": "CHECKOUT" if case["last_stage_index"] >= 1 else "CART",
        "createdAt": iso(start),
        "visitStartedAt": iso(start - timedelta(minutes=10)),
        "hoursSinceLastAbandonedCheckout": round(case["rng"].uniform(1, 96), 1),
        "inventoryAvailable": case["inventory_available"],
        "emailState": email_state,
        "emailSentAt": iso(start + timedelta(hours=10)) if email_state == "SENT" else None,
        "daysSinceLastAbandonmentEmail": case["rng"].randint(0, 7) if email_state == "SENT" else 0,
        "customerHasNoOrderSinceAbandonment": case["scenario"] != "completed",
        "customerHasNoDraftOrderSinceAbandonment": True,
        "isFromOnlineStore": True,
        "isFromCustomStorefront": False,
        "isFromShopApp": False,
        "isFromShopPay": case["rng"].random() < 0.2,
        "abandonedCheckoutPayload": checkout,
    }


def flat_row(case: dict, events: list[dict]) -> dict:
    seen = {event["name"] for event in events}
    alerts = {event["data"]["alert"]["type"] for event in events if event["name"] == "alert_displayed"}
    shipping_ratio = case["shipping_price"] / case["subtotal_price"] if case["subtotal_price"] else 0
    extra_ratio = (case["total_price"] - case["subtotal_price"]) / case["subtotal_price"]
    row = {
        "case_id": case["case_id"],
        "client_id": case["client_id"],
        "split": split_for(case["client_id"]),
        "cart_viewed": int("cart_viewed" in seen),
        "checkout_started": int("checkout_started" in seen),
        "contact_submitted": int("checkout_contact_info_submitted" in seen),
        "address_submitted": int("checkout_address_info_submitted" in seen),
        "shipping_submitted": int("checkout_shipping_info_submitted" in seen),
        "payment_submitted": int("payment_info_submitted" in seen),
        "checkout_completed": int("checkout_completed" in seen),
        "last_funnel_stage": FUNNEL[case["last_stage_index"]],
        "country_code": case["country_code"],
        "device_type": case["device_type"],
        "traffic_source": case["traffic_source"],
        "line_items_count": case["line_items_count"],
        "items_quantity": case["items_quantity"],
        "subtotal_price": case["subtotal_price"],
        "total_price": case["total_price"],
        "discount_amount": case["discount_amount"],
        "duties_amount": case["duties_amount"],
        "shipping_price": case["shipping_price"],
        "shipping_ratio": round(shipping_ratio, 4),
        "extra_cost_ratio": round(extra_ratio, 4),
        "previous_orders": case["previous_orders"],
        "is_first_time_customer": int(case["previous_orders"] == 0),
        "customer_amount_spent": case["customer_amount_spent"],
        "previous_abandonments": case["previous_abandonments"],
        "recovery_emails_sent": case["recovery_emails_sent"],
        "previous_reminder_recoveries": case["previous_reminder_recoveries"],
        "inventory_available": int(case["inventory_available"]),
        "payment_error": int("PAYMENT_ERROR" in alerts),
        "discount_error": int("DISCOUNT_ERROR" in alerts),
        "delivery_error": int("DELIVERY_ERROR" in alerts),
        "inventory_error": int("INVENTORY_ERROR" in alerts),
        "detected_pattern": case["scenario"],
        "recommended_action": case["recommended_action"],
    }
    return row


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def generate(count: int, seed: int, output_dir: Path) -> dict[str, int]:
    rng = random.Random(seed)
    raw_dir, processed_dir = output_dir / "raw", output_dir / "processed"
    raw_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)
    scenario_names = list(SCENARIOS)
    pixel_events, checkouts, abandonments, rows = [], [], [], []
    client_count = max(10, count // 3)

    for index in range(1, count + 1):
        scenario = scenario_names[(index - 1) % len(scenario_names)]
        client_number = 1 + ((index * 7919) % client_count)
        case = make_case(rng, index, client_number, scenario)
        start = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=rng.randint(0, 300_000))
        events = make_events(case, start)
        checkout = make_abandoned_checkout(case, start, datetime.fromisoformat(events[-1]["timestamp"].replace("Z", "+00:00")))
        pixel_events.extend(events)
        if checkout:
            checkouts.append(checkout)
        abandonments.append(make_abandonment(case, checkout, start))
        rows.append(flat_row(case, events))

    write_jsonl(raw_dir / "synthetic_pixel_events.jsonl", pixel_events)
    write_jsonl(raw_dir / "synthetic_abandoned_checkouts.jsonl", checkouts)
    write_jsonl(raw_dir / "synthetic_abandonments.jsonl", abandonments)
    write_csv(processed_dir / "recommendation_cases.csv", rows)
    for split in ("train", "validation", "test"):
        write_csv(processed_dir / f"{split}.csv", [row for row in rows if row["split"] == split])

    counts = Counter(row["split"] for row in rows)
    print(f"Generated {count:,} recommendation cases and {len(pixel_events):,} pixel events")
    print("Splits: " + ", ".join(f"{name}={counts[name]:,}" for name in ("train", "validation", "test")))
    print(f"Admin abandoned/recovered checkouts: {len(checkouts):,}")
    return dict(counts)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parents[1] / "data")
    args = parser.parse_args()
    if args.count < len(SCENARIOS):
        parser.error(f"--count must be at least {len(SCENARIOS)}")
    generate(args.count, args.seed, args.output_dir)


if __name__ == "__main__":
    main()
