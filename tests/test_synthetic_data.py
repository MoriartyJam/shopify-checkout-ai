import csv
import json
import tempfile
import unittest
from pathlib import Path

from src.generate_synthetic_data import FUNNEL, generate


class SyntheticDataTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.data_dir = Path(cls.temp_dir.name)
        generate(300, 42, cls.data_dir)
        with (cls.data_dir / "processed/recommendation_cases.csv").open() as file:
            cls.rows = list(csv.DictReader(file))
        with (cls.data_dir / "raw/synthetic_pixel_events.jsonl").open() as file:
            cls.events = [json.loads(line) for line in file]
        with (cls.data_dir / "raw/synthetic_abandoned_checkouts.jsonl").open() as file:
            cls.checkouts = [json.loads(line) for line in file]

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def test_all_splits_exist_and_clients_do_not_leak(self):
        clients = {}
        for row in self.rows:
            clients.setdefault(row["client_id"], set()).add(row["split"])
        self.assertEqual({"train", "validation", "test"}, {row["split"] for row in self.rows})
        self.assertTrue(all(len(splits) == 1 for splits in clients.values()))

    def test_pixel_top_level_shape(self):
        allowed = {"id", "name", "clientId", "timestamp", "seq", "type", "context", "data"}
        names = set(FUNNEL) | {"alert_displayed"}
        self.assertTrue(all(set(event) == allowed for event in self.events))
        self.assertTrue(all(event["name"] in names for event in self.events))

    def test_no_personal_fields(self):
        serialized = json.dumps(self.events + self.checkouts)
        for forbidden in ("email", "phone", "firstName", "lastName", "address1", "address2"):
            self.assertNotIn(f'"{forbidden}"', serialized)

    def test_technical_errors_never_recommend_discount(self):
        error_rows = [row for row in self.rows if row["payment_error"] == "1" or row["inventory_error"] == "1"]
        self.assertTrue(error_rows)
        self.assertTrue(all("PERCENT" not in row["recommended_action"] for row in error_rows))

    def test_completed_checkout_has_no_action(self):
        completed = [row for row in self.rows if row["checkout_completed"] == "1"]
        self.assertTrue(completed)
        self.assertTrue(all(row["recommended_action"] == "NO_ACTION" for row in completed))

    def test_early_abandonment_has_no_admin_checkout(self):
        expected = sum(int(row["contact_submitted"]) for row in self.rows)
        self.assertEqual(expected, len(self.checkouts))


if __name__ == "__main__":
    unittest.main()
