"""Normalize minimal Shopify Admin responses into privacy-safe checkout facts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


def pseudonym(customer_id: str | None) -> str:
    if not customer_id:
        return "guest"
    digest = hashlib.sha256(customer_id.encode()).hexdigest()[:16]
    return f"shopify-customer-{digest}"


def checkout_token_from_recovery_url(value: str) -> str | None:
    parts = [part for part in urlparse(value).path.split("/") if part]
    try:
        recover_index = parts.index("recover")
    except ValueError:
        return None
    if recover_index == 0 or "checkouts" not in parts[:recover_index]:
        return None
    return parts[recover_index - 1]


def nodes(payload: dict[str, Any], connection: str) -> list[dict[str, Any]]:
    data = payload.get("data") or payload
    value = data.get(connection) or {}
    return value.get("nodes") or []


def normalize_admin_pages(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    facts: dict[str, dict[str, Any]] = {}
    for page in pages:
        for order in nodes(page, "orders"):
            token = order.get("checkoutToken")
            if not token:
                continue
            customer = order.get("customer") or {}
            current_orders = int(customer.get("numberOfOrders") or 0)
            facts[str(token)] = {
                "checkoutToken": str(token),
                "outcome": "completed",
                "orderId": order.get("id"),
                "customerCohortId": pseudonym(customer.get("id")),
                "previousOrderCount": max(0, current_orders - 1),
                "source": "shopify_admin_orders",
                "observedAt": order.get("createdAt"),
            }
        for checkout in nodes(page, "abandonedCheckouts"):
            token = checkout_token_from_recovery_url(str(checkout.get("abandonedCheckoutUrl") or ""))
            if not token or token in facts:
                continue
            customer = checkout.get("customer") or {}
            facts[token] = {
                "checkoutPathToken": token,
                "outcome": "abandoned",
                "abandonedCheckoutId": checkout.get("id"),
                "customerCohortId": pseudonym(customer.get("id")),
                "previousOrderCount": int(customer.get("numberOfOrders") or 0),
                "source": "shopify_admin_abandoned_checkouts",
                "observedAt": checkout.get("updatedAt") or checkout.get("createdAt"),
            }
    return sorted(
        facts.values(),
        key=lambda row: row.get("checkoutToken") or row.get("checkoutPathToken") or "",
    )


def read_pages(paths: list[Path]) -> list[dict[str, Any]]:
    pages = []
    for path in paths:
        value = json.loads(path.read_text(encoding="utf-8"))
        pages.extend(value if isinstance(value, list) else [value])
    return pages


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def main() -> None:
    project = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("responses", nargs="+", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=project / "data/raw/shopify_admin_checkout_facts.jsonl",
    )
    args = parser.parse_args()
    facts = normalize_admin_pages(read_pages(args.responses))
    write_jsonl(args.output, facts)
    print(json.dumps({"facts": len(facts), "output": str(args.output)}, indent=2))


if __name__ == "__main__":
    main()
