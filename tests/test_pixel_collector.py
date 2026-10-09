import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import web_app


class PixelCollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.event_file = Path(self.temp_dir.name) / "pixel-events.jsonl"
        self.intervention_file = Path(self.temp_dir.name) / "interventions.jsonl"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_persists_supported_event(self):
        with patch.object(web_app, "PIXEL_EVENTS", self.event_file):
            response = web_app.store_pixel_event(
                {
                    "schemaVersion": 1,
                    "eventId": "evt-1",
                    "eventName": "checkout_started",
                    "clientId": "client-1",
                    "checkout": {"lineItemCount": 2},
                },
            )

        self.assertEqual(response["status"], "accepted")
        record = json.loads(self.event_file.read_text(encoding="utf-8"))
        self.assertEqual(record["eventId"], "evt-1")
        self.assertEqual(record["eventName"], "checkout_started")
        self.assertIn("receivedAt", record)

    def test_rejects_unknown_event(self):
        with patch.object(web_app, "PIXEL_EVENTS", self.event_file):
            with self.assertRaises(web_app.HTTPException) as raised:
                web_app.store_pixel_event(
                    {"eventId": "evt-2", "eventName": "customer_created"}
                )

        self.assertEqual(raised.exception.status_code, 422)
        self.assertFalse(self.event_file.exists())

    def test_real_cases_endpoint_shape(self):
        result = web_app.real_cases()
        self.assertGreaterEqual(result["count"], 1)
        self.assertIn(result["items"][0]["outcome"], {"abandoned", "completed"})
        self.assertIn("customer_segment", result["items"][0])

    def test_real_recommendation_uses_explainable_rules(self):
        result = web_app.real_recommendation("real-test-003")
        self.assertEqual("SEND_CHECKOUT_REMINDER", result["recommended_action"])
        self.assertEqual("customer_returning", result["customer_segment"])
        self.assertEqual(1, result["previous_orders"])

    def test_records_action_and_updates_result(self):
        payload = web_app.InterventionInput(
            action="REMINDER_SENT", channel="email", result="pending"
        )
        with patch.object(web_app, "INTERVENTIONS", self.intervention_file):
            created = web_app.store_intervention("real-test-003", payload)
            updated = web_app.update_intervention_result(
                created["intervention_id"],
                web_app.InterventionResultInput(result="recovered"),
            )
            records = web_app.load_interventions("real-test-003")

        self.assertEqual("recovered", updated["result"])
        self.assertEqual(1, len(records))
        self.assertEqual("REMINDER_SENT", records[0]["action"])
        self.assertIn("result_recorded_at", records[0])

    def test_rejects_unsupported_administrator_action(self):
        payload = web_app.InterventionInput(action="SEND_MONEY", channel="email")
        with patch.object(web_app, "INTERVENTIONS", self.intervention_file):
            with self.assertRaises(web_app.HTTPException) as raised:
                web_app.store_intervention("real-test-003", payload)
        self.assertEqual(422, raised.exception.status_code)


if __name__ == "__main__":
    unittest.main()
