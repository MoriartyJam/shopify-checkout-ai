import unittest

from src.outcome_analytics import build_outcome_analytics


class OutcomeAnalyticsTests(unittest.TestCase):
    def test_uses_latest_intervention_per_checkout(self):
        cases = [
            {
                "case_id": "checkout-1",
                "outcome": "abandoned",
                "customer_segment": "customer_returning",
                "detected_pattern": "drop_after_shipping",
            }
        ]
        interventions = [
            {
                "case_id": "checkout-1",
                "action": "REMINDER_SENT",
                "result": "pending",
                "recorded_at": "2026-10-01T10:00:00Z",
            },
            {
                "case_id": "checkout-1",
                "action": "FREE_SHIPPING_OFFERED",
                "result": "recovered",
                "recorded_at": "2026-10-02T10:00:00Z",
            },
        ]

        result = build_outcome_analytics(interventions, cases)

        self.assertEqual(1, result["summary"]["total_interventions"])
        self.assertEqual("FREE_SHIPPING_OFFERED", result["records"][0]["action"])
        self.assertEqual(1.0, result["summary"]["recovery_rate"])

    def test_shopify_completion_resolves_pending_action(self):
        cases = [
            {
                "case_id": "checkout-2",
                "outcome": "completed",
                "customer_segment": "customer_new",
                "detected_pattern": "completed",
            }
        ]
        interventions = [
            {
                "case_id": "checkout-2",
                "action": "REMINDER_SENT",
                "result": "pending",
                "recorded_at": "2026-10-03T10:00:00Z",
            }
        ]

        result = build_outcome_analytics(interventions, cases)

        self.assertEqual("recovered", result["records"][0]["result"])
        self.assertEqual("shopify", result["records"][0]["result_source"])

    def test_excludes_interventions_without_matching_checkout(self):
        result = build_outcome_analytics(
            [
                {
                    "case_id": "missing",
                    "action": "REMINDER_SENT",
                    "result": "recovered",
                    "recorded_at": "2026-10-03T10:00:00Z",
                }
            ],
            [],
        )

        self.assertEqual(0, result["summary"]["total_interventions"])
        self.assertEqual([], result["by_action"])


if __name__ == "__main__":
    unittest.main()
