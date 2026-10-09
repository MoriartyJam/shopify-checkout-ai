"""Transparent baseline recommender for Shopify abandonment scenarios."""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class Recommendation:
    pattern: str
    action: str
    confidence: str
    reasons: list[str]
    alternative: str | None = None
    prohibited_action: str | None = None


def number(row: Mapping[str, str], key: str) -> float:
    value = row.get(key, "")
    return float(value) if value not in (None, "") else 0.0


def flag(row: Mapping[str, str], key: str) -> bool:
    return number(row, key) == 1


def recommend(row: Mapping[str, str]) -> Recommendation:
    """Return one primary, explainable recommendation from derived features."""

    # 1. Never contact a customer about a checkout that is already complete.
    if flag(row, "checkout_completed"):
        return Recommendation(
            "completed", "NO_ACTION", "high",
            ["Заказ уже завершён."], prohibited_action="SEND_CHECKOUT_REMINDER",
        )

    # 2. Explicit Shopify errors outrank commercial hypotheses.
    if flag(row, "payment_error"):
        return Recommendation(
            "payment_error", "SUGGEST_ALTERNATIVE_PAYMENT", "high",
            ["Shopify зарегистрировал PAYMENT_ERROR после отправки платёжных данных."],
            alternative="CHECK_PAYMENT_PROVIDER", prohibited_action="OFFER_PERCENT_DISCOUNT",
        )
    if flag(row, "discount_error"):
        return Recommendation(
            "discount_error", "CHECK_DISCOUNT_CODE", "high",
            ["Shopify зарегистрировал DISCOUNT_ERROR."],
            alternative="SEND_CHECKOUT_REMINDER", prohibited_action="OFFER_ANOTHER_UNVERIFIED_CODE",
        )
    inventory_known_unavailable = (
        row.get("inventory_available") not in (None, "")
        and not flag(row, "inventory_available")
    )
    if flag(row, "inventory_error") or inventory_known_unavailable:
        return Recommendation(
            "inventory_error", "CHECK_PRODUCT_STOCK", "high",
            ["Товар недоступен или Shopify зарегистрировал INVENTORY_ERROR."],
            alternative="NOTIFY_WHEN_RESTOCKED", prohibited_action="OFFER_PERCENT_DISCOUNT",
        )

    # 3. Communication history prevents excessive messaging and discounts.
    if number(row, "recovery_emails_sent") >= 3:
        return Recommendation(
            "message_non_responder", "STOP_MESSAGES", "high",
            ["Клиент уже получил не менее трёх recovery-сообщений без завершения заказа."],
            prohibited_action="SEND_ANOTHER_MESSAGE",
        )
    if number(row, "previous_abandonments") >= 3:
        return Recommendation(
            "repeated_abandonment", "AVOID_AUTOMATIC_DISCOUNT", "medium",
            ["У клиента несколько предыдущих abandonment-событий."],
            alternative="SEND_CHECKOUT_REMINDER",
        )
    if number(row, "previous_reminder_recoveries") >= 1:
        return Recommendation(
            "reminder_responsive", "SEND_CHECKOUT_REMINDER", "high",
            ["Ранее клиент завершал покупку после обычного напоминания."],
            prohibited_action="OFFER_PERCENT_DISCOUNT",
        )

    # 4. Cart economics refine the recommendation after technical blockers.
    if flag(row, "shipping_submitted") and number(row, "shipping_ratio") >= 0.25:
        return Recommendation(
            "high_shipping_ratio", "OFFER_FREE_SHIPPING", "high",
            [f"Доставка составляет {number(row, 'shipping_ratio'):.0%} стоимости товаров."],
            alternative="OFFER_CHEAPER_DELIVERY", prohibited_action="OFFER_PERCENT_DISCOUNT",
        )
    if number(row, "duties_amount") > 0 and number(row, "extra_cost_ratio") >= 0.15:
        return Recommendation(
            "high_extra_costs", "REVIEW_EXTRA_COSTS", "medium",
            [f"Дополнительные расходы увеличили subtotal на {number(row, 'extra_cost_ratio'):.0%}."],
            alternative="OFFER_FREE_SHIPPING",
        )
    if number(row, "subtotal_price") >= 350:
        return Recommendation(
            "high_cart_value", "OFFER_PERSONAL_HELP", "medium",
            ["Стоимость корзины значительно выше стартового порога $350."],
            alternative="SEND_CHECKOUT_REMINDER",
        )

    # 5. The last observed funnel stage supplies the baseline advice.
    stage = row.get("last_funnel_stage", "")
    by_stage = {
        "cart_viewed": Recommendation(
            "cart_not_checkout", "REVIEW_CART_VALUE", "medium",
            ["Корзина просмотрена, но checkout не начат."],
            alternative="SEND_CART_REMINDER",
        ),
        "checkout_started": Recommendation(
            "drop_after_checkout_start", "REVIEW_CHECKOUT_ENTRY", "medium",
            ["Checkout начат, но контактная информация не отправлена."],
        ),
        "checkout_contact_info_submitted": Recommendation(
            "drop_after_contact", "SEND_CHECKOUT_REMINDER", "medium",
            ["Контакт отправлен, но адрес не заполнен."],
        ),
        "checkout_address_info_submitted": Recommendation(
            "drop_after_address", "CHECK_DELIVERY_SETTINGS", "medium",
            ["Адрес отправлен, но вариант доставки не выбран."],
            alternative="SEND_CHECKOUT_REMINDER",
        ),
        "checkout_shipping_info_submitted": Recommendation(
            "drop_after_shipping", "SEND_CHECKOUT_REMINDER", "medium",
            ["Доставка выбрана, но платёжные данные не отправлены."],
            alternative="REVIEW_EXTRA_COSTS",
        ),
        "payment_info_submitted": Recommendation(
            "drop_after_payment", "CHECK_PAYMENT_PROVIDER", "medium",
            ["Платёжные данные отправлены, но checkout не завершён."],
            alternative="SUGGEST_ALTERNATIVE_PAYMENT",
        ),
    }
    return by_stage.get(stage, Recommendation(
        "unknown", "REVIEW_MANUALLY", "low",
        ["Недостаточно подтверждённых данных для автоматического совета."],
    ))


def analyze(input_path: Path, output_path: Path) -> tuple[int, int]:
    with input_path.open(encoding="utf-8") as file:
        rows = list(csv.DictReader(file))
    correct = 0
    with output_path.open("w", encoding="utf-8") as file:
        for row in rows:
            result = recommend(row)
            correct += int(result.action == row.get("recommended_action"))
            payload = {"case_id": row.get("case_id"), **asdict(result)}
            file.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return correct, len(rows)


def main() -> None:
    project = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=project / "data/processed/test.csv")
    parser.add_argument("--output", type=Path, default=project / "data/processed/recommendations.jsonl")
    args = parser.parse_args()
    correct, total = analyze(args.input, args.output)
    print(f"Generated {total:,} recommendations: {args.output}")
    if total:
        print(f"Baseline action agreement with synthetic labels: {correct / total:.1%}")


if __name__ == "__main__":
    main()
