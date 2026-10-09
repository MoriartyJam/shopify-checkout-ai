"""Run the trained hybrid recommender for one prepared Shopify case."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import tensorflow as tf

try:  # Package import: from src.predict_recommendation import ...
    from .recommendation_engine import recommend
    from .train_tensorflow import PATTERN_ACTIONS, encode_features
except ImportError:  # Script execution: python src/predict_recommendation.py
    from recommendation_engine import recommend
    from train_tensorflow import PATTERN_ACTIONS, encode_features


PATTERN_TEXT = {
    "cart_not_checkout": ("Корзина не перешла в checkout", "Проверить общую стоимость и мягко напомнить о корзине."),
    "drop_after_checkout_start": ("Остановка в начале checkout", "Проверить первый экран и сложность начала оформления."),
    "drop_after_contact": ("Остановка после контакта", "Отправить обычное напоминание продолжить checkout."),
    "drop_after_address": ("Остановка после адреса", "Проверить доступность и настройки доставки для региона."),
    "drop_after_shipping": ("Остановка после доставки", "Напомнить о checkout и проверить итоговые расходы."),
    "high_shipping_ratio": ("Высокая доля доставки", "Рассмотреть бесплатную или более дешёвую доставку."),
    "high_extra_costs": ("Высокие дополнительные расходы", "Проверить налоги, пошлины и прозрачность итоговой цены."),
    "payment_error": ("Ошибка оплаты", "Предложить другой способ оплаты и проверить провайдера."),
    "discount_error": ("Ошибка скидки", "Проверить промокод и его условия."),
    "inventory_error": ("Проблема с наличием", "Проверить остатки или предложить уведомление о поступлении."),
    "high_cart_value": ("Высокая стоимость корзины", "Предложить персональную помощь без автоматической скидки."),
    "repeated_abandonment": ("Повторные отказы", "Не выдавать скидку автоматически; проверить историю реакций."),
    "reminder_responsive": ("Клиент реагирует на напоминания", "Повторить обычное напоминание без скидки."),
    "message_non_responder": ("Нет реакции на сообщения", "Остановить повторные recovery-сообщения."),
    "completed": ("Checkout завершён", "Ничего не отправлять."),
}

HARD_GUARDRAILS = {
    "NO_ACTION", "STOP_MESSAGES", "SUGGEST_ALTERNATIVE_PAYMENT",
    "CHECK_DISCOUNT_CODE", "CHECK_PRODUCT_STOCK",
}


class HybridPredictor:
    def __init__(self, project: Path):
        self.project = project
        self.model = tf.keras.models.load_model(project / "models/abandonment_pattern_model.keras")
        self.schema = json.loads((project / "models/metadata.json").read_text(encoding="utf-8"))

    def predict(self, row: dict[str, str]) -> dict:
        features = encode_features([row], self.schema)
        probabilities = self.model.predict(features, verbose=0)[0]
        predicted_index = int(probabilities.argmax())
        model_pattern = self.schema["labels"][predicted_index]
        model_action = PATTERN_ACTIONS[model_pattern]
        baseline = recommend(row)

        # Deterministic safety rules override ML for completed orders, explicit
        # Shopify errors and communication suppression.
        guardrail_applied = baseline.action in HARD_GUARDRAILS
        final_pattern = baseline.pattern if guardrail_applied else model_pattern
        final_action = baseline.action if guardrail_applied else model_action
        title, advice = PATTERN_TEXT.get(final_pattern, ("Требуется проверка", "Проверить случай вручную."))
        reasons = baseline.reasons if baseline.pattern == final_pattern else [
            "Закономерность определена TensorFlow по совокупности признаков checkout."
        ]
        return {
            "case_id": row.get("case_id"),
            "title": title,
            "advice": advice,
            "recommended_action": final_action,
            "confidence": round(float(probabilities[predicted_index]), 4),
            "reasons": reasons,
            "alternative": baseline.alternative,
            "prohibited_action": baseline.prohibited_action,
            "model_pattern": model_pattern,
            "guardrail_applied": guardrail_applied,
        }


def load_case(path: Path, case_id: str | None) -> dict[str, str]:
    if path.suffix == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    with path.open(encoding="utf-8") as file:
        rows = csv.DictReader(file)
        for row in rows:
            if case_id is None or row.get("case_id") == case_id:
                return row
    raise SystemExit(f"Case not found: {case_id}")


def main() -> None:
    project = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=project / "data/processed/test.csv")
    parser.add_argument("--case-id")
    args = parser.parse_args()
    row = load_case(args.input, args.case_id)
    result = HybridPredictor(project).predict(row)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
