import unittest

from src.build_real_dataset import build_cases


class RealDatasetBuilderTests(unittest.TestCase):
    def test_builds_completed_and_annotated_abandoned_cases(self):
        events = [
            self.event("a", "checkout_started", "2026-01-01T00:00:00Z"),
            self.event("a", "checkout_completed", "2026-01-01T00:05:00Z", order_id="1"),
            self.event("b", "checkout_started", "2026-01-02T00:00:00Z"),
            self.event("b", "checkout_address_info_submitted", "2026-01-02T00:03:00Z"),
        ]
        scenarios = [
            {"scenarioId": "one", "checkoutToken": "a", "expectedOutcome": "completed"},
            {"scenarioId": "two", "checkoutToken": "b", "expectedOutcome": "abandoned"},
        ]
        rows = {row["checkout_token"]: row for row in build_cases(events, scenarios)}
        self.assertEqual("completed", rows["a"]["outcome"])
        self.assertEqual("observed_pixel", rows["a"]["label_source"])
        self.assertEqual("abandoned", rows["b"]["outcome"])
        self.assertEqual("checkout_address_info_submitted", rows["b"]["last_funnel_stage"])

    def test_attaches_tokenless_alert_to_preceding_checkout(self):
        events = [
            self.event("a", "checkout_started", "2026-01-01T00:00:00Z"),
            {
                "eventName": "alert_displayed",
                "timestamp": "2026-01-01T00:01:00Z",
                "clientId": "client-1",
                "checkout": {},
                "alert": {"target": "cart.paymentLines[0].billingAddress.city"},
            },
        ]
        row = build_cases(events, [{"checkoutToken": "a", "expectedOutcome": "abandoned"}])[0]
        self.assertEqual(1, row["payment_error"])
        self.assertEqual("payment_error", row["detected_pattern"])

    @staticmethod
    def event(token, name, timestamp, order_id=None):
        return {
            "eventName": name,
            "timestamp": timestamp,
            "clientId": "client-1",
            "checkout": {
                "token": token,
                "orderId": order_id,
                "subtotalPrice": {"amount": "10"},
                "totalPrice": {"amount": "12"},
                "shippingLinePrice": {"amount": "2"},
                "lineItemCount": 1,
                "quantity": 1,
            },
        }


if __name__ == "__main__":
    unittest.main()
