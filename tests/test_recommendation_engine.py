import unittest

from src.recommendation_engine import recommend


def base(**changes):
    row = {
        "checkout_completed": "0", "payment_error": "0", "discount_error": "0",
        "inventory_error": "0", "inventory_available": "1", "recovery_emails_sent": "0",
        "previous_abandonments": "0", "previous_reminder_recoveries": "0",
        "shipping_submitted": "0", "shipping_ratio": "0", "duties_amount": "0",
        "extra_cost_ratio": "0", "subtotal_price": "50",
        "last_funnel_stage": "checkout_started",
    }
    row.update(changes)
    return row


class RecommendationEngineTest(unittest.TestCase):
    def test_completed_checkout_stops_messages(self):
        result = recommend(base(checkout_completed="1", payment_error="1"))
        self.assertEqual("NO_ACTION", result.action)

    def test_payment_error_outranks_customer_history(self):
        result = recommend(base(payment_error="1", previous_abandonments="5"))
        self.assertEqual("SUGGEST_ALTERNATIVE_PAYMENT", result.action)
        self.assertEqual("OFFER_PERCENT_DISCOUNT", result.prohibited_action)

    def test_non_responder_stops_messages(self):
        self.assertEqual("STOP_MESSAGES", recommend(base(recovery_emails_sent="3")).action)

    def test_high_shipping_recommends_free_shipping(self):
        result = recommend(base(shipping_submitted="1", shipping_ratio="0.35"))
        self.assertEqual("OFFER_FREE_SHIPPING", result.action)

    def test_funnel_stage_produces_baseline_advice(self):
        result = recommend(base(last_funnel_stage="checkout_address_info_submitted"))
        self.assertEqual("CHECK_DELIVERY_SETTINGS", result.action)

    def test_missing_inventory_data_is_not_treated_as_out_of_stock(self):
        row = base(last_funnel_stage="checkout_shipping_info_submitted")
        row.pop("inventory_available")
        self.assertEqual("SEND_CHECKOUT_REMINDER", recommend(row).action)


if __name__ == "__main__":
    unittest.main()
