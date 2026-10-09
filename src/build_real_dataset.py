"""Build privacy-safe checkout cases from real Shopify pixel test events."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


FUNNEL = [
    "cart_viewed",
    "checkout_started",
    "checkout_contact_info_submitted",
    "checkout_address_info_submitted",
    "checkout_shipping_info_submitted",
    "payment_info_submitted",
    "checkout_completed",
]
STAGE_INDEX = {name: index for index, name in enumerate(FUNNEL)}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as file:
        return [json.loads(line) for line in file if line.strip()]


def as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def amount(checkout: dict[str, Any], field: str) -> float:
    value = checkout.get(field)
    return as_float(value.get("amount")) if isinstance(value, dict) else 0.0


def classify_alert(alert: dict[str, Any]) -> str | None:
    text = " ".join(str(alert.get(key, "")) for key in ("type", "target", "message")).lower()
    if any(word in text for word in ("payment", "card", "billing")):
        return "payment_error"
    if any(word in text for word in ("discount", "promo", "reduction")):
        return "discount_error"
    if any(word in text for word in ("shipping", "delivery", "postal", "zip", "address")):
        return "delivery_error"
    if any(word in text for word in ("inventory", "stock", "sold out")):
        return "inventory_error"
    return None


def pattern_and_action(last_stage: str, errors: set[str], completed: bool) -> tuple[str, str]:
    if completed:
        return "completed", "NO_ACTION"
    priorities = [
        ("payment_error", "SUGGEST_ALTERNATIVE_PAYMENT"),
        ("inventory_error", "CHECK_PRODUCT_STOCK"),
        ("discount_error", "CHECK_DISCOUNT_CODE"),
        ("delivery_error", "CHECK_DELIVERY_SETTINGS"),
    ]
    for pattern, action in priorities:
        if pattern in errors:
            return pattern, action
    mapping = {
        "cart_viewed": ("cart_not_checkout", "REVIEW_CART_VALUE"),
        "checkout_started": ("drop_after_checkout_start", "REVIEW_CHECKOUT_ENTRY"),
        "checkout_contact_info_submitted": ("drop_after_contact", "SEND_CHECKOUT_REMINDER"),
        "checkout_address_info_submitted": ("drop_after_address", "CHECK_DELIVERY_SETTINGS"),
        "checkout_shipping_info_submitted": ("drop_after_shipping", "SEND_CHECKOUT_REMINDER"),
        "payment_info_submitted": ("drop_after_payment", "SUGGEST_ALTERNATIVE_PAYMENT"),
    }
    return mapping.get(last_stage, ("unknown", "REVIEW_MANUALLY"))


def build_cases(
    events: list[dict[str, Any]],
    scenarios: list[dict[str, Any]],
    admin_facts: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    scenario_by_token = {row["checkoutToken"]: row for row in scenarios}
    admin_by_token = {
        row["checkoutToken"]: row for row in (admin_facts or []) if row.get("checkoutToken")
    }
    admin_by_path = {
        row["checkoutPathToken"]: row
        for row in (admin_facts or [])
        if row.get("checkoutPathToken")
    }
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    tokenless_by_client: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for event in events:
        checkout = event.get("checkout") or {}
        token = checkout.get("token")
        if token:
            grouped[str(token)].append(event)
        elif event.get("clientId"):
            tokenless_by_client[str(event["clientId"])].append(event)

    # Attribute tokenless alerts to the nearest preceding checkout of the same pixel client.
    for client_id, tokenless in tokenless_by_client.items():
        candidates = []
        for token, rows in grouped.items():
            if any(str(row.get("clientId")) == client_id for row in rows):
                first = min(str(row.get("timestamp", "")) for row in rows)
                candidates.append((first, token))
        for event in tokenless:
            timestamp = str(event.get("timestamp", ""))
            preceding = [(start, token) for start, token in candidates if start <= timestamp]
            if preceding:
                grouped[max(preceding)[1]].append(event)

    rows = []
    for token in sorted(set(grouped) | set(scenario_by_token)):
        token_events = sorted(grouped.get(token, []), key=lambda row: str(row.get("timestamp", "")))
        scenario = scenario_by_token.get(token, {})
        path_token = scenario.get("checkoutPathToken") or next(
            (event.get("checkoutPathToken") for event in token_events if event.get("checkoutPathToken")),
            None,
        )
        admin = admin_by_token.get(token) or admin_by_path.get(path_token) or {}
        seen = {str(event.get("eventName")) for event in token_events}
        observed_stages = [stage for stage in FUNNEL if stage in seen]
        last_stage = observed_stages[-1] if observed_stages else "unknown"
        latest_checkout = next(
            (event.get("checkout") or {} for event in reversed(token_events) if (event.get("checkout") or {}).get("token")),
            {},
        )
        errors = {
            error
            for event in token_events
            if event.get("eventName") == "alert_displayed"
            for error in [classify_alert(event.get("alert") or {})]
            if error
        }
        observed_completed = "checkout_completed" in seen
        annotated_outcome = str(scenario.get("expectedOutcome", "unknown"))
        outcome = "completed" if observed_completed else str(admin.get("outcome", annotated_outcome))
        previous_orders = int(admin.get("previousOrderCount", scenario.get("previousOrderCount", 0)))
        customer_cohort = admin.get("customerCohortId", scenario.get("customerCohortId", "unknown"))
        customer_segment = scenario.get("customerSegment")
        if not customer_segment and customer_cohort == "guest":
            customer_segment = "guest_new"
        if not customer_segment:
            customer_segment = "customer_returning" if previous_orders else "customer_new"
        pattern, action = pattern_and_action(last_stage, errors, observed_completed)
        subtotal = amount(latest_checkout, "subtotalPrice")
        total = amount(latest_checkout, "totalPrice")
        shipping = amount(latest_checkout, "shippingLinePrice")
        rows.append({
            "case_id": scenario.get("scenarioId", f"real-{token[:12]}"),
            "checkout_token": token,
            "customer_cohort_id": customer_cohort,
            "customer_segment": customer_segment,
            "previous_orders": previous_orders,
            "intervention": scenario.get("intervention", "unknown"),
            "outcome": outcome,
            "label_source": (
                "observed_pixel" if observed_completed
                else "shopify_admin" if admin.get("outcome")
                else "test_annotation"
            ),
            "cart_viewed": int("cart_viewed" in seen),
            "checkout_started": int("checkout_started" in seen),
            "contact_submitted": int("checkout_contact_info_submitted" in seen),
            "address_submitted": int("checkout_address_info_submitted" in seen),
            "shipping_submitted": int("checkout_shipping_info_submitted" in seen),
            "payment_submitted": int("payment_info_submitted" in seen),
            "checkout_completed": int(observed_completed),
            "last_funnel_stage": last_stage,
            "line_items_count": int(latest_checkout.get("lineItemCount") or 0),
            "items_quantity": int(latest_checkout.get("quantity") or 0),
            "subtotal_price": subtotal,
            "total_price": total,
            "shipping_price": shipping,
            "shipping_ratio": round(shipping / subtotal, 4) if subtotal else 0.0,
            "extra_cost_ratio": round((total - subtotal) / subtotal, 4) if subtotal else 0.0,
            "payment_error": int("payment_error" in errors),
            "discount_error": int("discount_error" in errors),
            "delivery_error": int("delivery_error" in errors),
            "inventory_error": int("inventory_error" in errors),
            "detected_pattern": pattern,
            "recommended_action": action,
            "first_event_at": token_events[0].get("timestamp", "") if token_events else "",
            "last_event_at": token_events[-1].get("timestamp", "") if token_events else "",
            "event_count": len(token_events),
            "order_id": latest_checkout.get("orderId") or admin.get("orderId") or scenario.get("orderId", ""),
        })
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        raise ValueError("No real checkout cases were built")
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    project = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", type=Path, default=project / "data/raw/real_pixel_events.jsonl")
    parser.add_argument("--scenarios", type=Path, default=project / "data/raw/test_scenarios.jsonl")
    parser.add_argument(
        "--admin-facts",
        type=Path,
        default=project / "data/raw/shopify_admin_checkout_facts.jsonl",
    )
    parser.add_argument("--output", type=Path, default=project / "data/processed/real_checkout_cases.csv")
    args = parser.parse_args()
    rows = build_cases(
        read_jsonl(args.events),
        read_jsonl(args.scenarios),
        read_jsonl(args.admin_facts),
    )
    write_csv(args.output, rows)
    outcomes = defaultdict(int)
    for row in rows:
        outcomes[row["outcome"]] += 1
    print(json.dumps({"cases": len(rows), "outcomes": dict(outcomes), "output": str(args.output)}, indent=2))


if __name__ == "__main__":
    main()
