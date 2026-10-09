import json
import unittest

from src.import_shopify_admin import (
    checkout_token_from_recovery_url,
    normalize_admin_pages,
)


class ShopifyAdminImportTests(unittest.TestCase):
    def test_extracts_token_without_retaining_recovery_url(self):
        url = "https://example.myshopify.com/checkouts/cn/token-123/recover?key=secret"
        self.assertEqual("token-123", checkout_token_from_recovery_url(url))

    def test_normalizes_orders_and_abandoned_checkouts_without_pii(self):
        page = {"data": {
            "orders": {"nodes": [{
                "id": "gid://shopify/Order/1",
                "checkoutToken": "completed-token",
                "createdAt": "2026-01-01T00:00:00Z",
                "customer": {"id": "gid://shopify/Customer/1", "numberOfOrders": "2"},
            }]},
            "abandonedCheckouts": {"nodes": [{
                "id": "gid://shopify/AbandonedCheckout/2",
                "abandonedCheckoutUrl": "https://shop.test/checkouts/abandoned-token/recover?key=secret",
                "updatedAt": "2026-01-02T00:00:00Z",
                "customer": {"id": "gid://shopify/Customer/1", "numberOfOrders": "2"},
            }]},
        }}
        facts = normalize_admin_pages([page])
        self.assertEqual({"completed", "abandoned"}, {row["outcome"] for row in facts})
        self.assertEqual(1, next(row for row in facts if row["outcome"] == "completed")["previousOrderCount"])
        serialized = json.dumps(facts)
        self.assertNotIn("recover", serialized)
        self.assertNotIn("gid://shopify/Customer/1", serialized)
        self.assertNotIn("secret", serialized)
        abandoned = next(row for row in facts if row["outcome"] == "abandoned")
        self.assertEqual("abandoned-token", abandoned["checkoutPathToken"])
        self.assertNotIn("checkoutToken", abandoned)


if __name__ == "__main__":
    unittest.main()
