import unittest
from datetime import datetime, timezone

from pydantic import ValidationError

from app.data.catalog import MERCHANTS, CATEGORY_NAMES
from app.data.geo import CITIES, city_distance_km
from app.data.schema import Transaction


def valid_payload(**over):
    p = dict(
        transaction_id="t1",
        user_id="u1",
        amount=59.9,
        currency="BRL",
        country="BR",
        city="São Paulo",
        device_id="dev_a",
        merchant_id="m_001",
        merchant_category="groceries",
        timestamp="2026-01-01T12:00:00Z",
    )
    p.update(over)
    return p


class SchemaTest(unittest.TestCase):
    def test_valid_transaction(self):
        t = Transaction(**valid_payload())
        self.assertEqual(t.timestamp, datetime(2026, 1, 1, 12, tzinfo=timezone.utc))
        self.assertIsNone(t.is_fraud)

    def test_naive_timestamp_is_utc(self):
        t = Transaction(**valid_payload(timestamp="2026-01-01T12:00:00"))
        self.assertEqual(t.timestamp.tzinfo, timezone.utc)

    def test_missing_required_field(self):
        p = valid_payload()
        del p["device_id"]
        with self.assertRaises(ValidationError):
            Transaction(**p)

    def test_non_positive_amount(self):
        for bad in (0, -5, -0.01):
            with self.assertRaises(ValidationError):
                Transaction(**valid_payload(amount=bad))

    def test_unknown_city_or_mismatched_country(self):
        with self.assertRaises(ValidationError):
            Transaction(**valid_payload(city="Atlantis"))
        with self.assertRaises(ValidationError):
            Transaction(**valid_payload(country="PT"))  # São Paulo não é PT

    def test_unknown_category(self):
        with self.assertRaises(ValidationError):
            Transaction(**valid_payload(merchant_category="weapons"))

    def test_event_omits_label_by_default(self):
        t = Transaction(**valid_payload(is_fraud=True, fraud_pattern="velocity"))
        self.assertNotIn("is_fraud", t.to_event())
        self.assertTrue(t.to_event(with_label=True)["is_fraud"])


class CatalogTest(unittest.TestCase):
    def test_every_category_has_a_merchant(self):
        present = {c for _, c in MERCHANTS}
        self.assertEqual(present, set(CATEGORY_NAMES))
        self.assertEqual(len({m for m, _ in MERCHANTS}), len(MERCHANTS))

    def test_distances(self):
        self.assertAlmostEqual(city_distance_km("São Paulo", "São Paulo"), 0.0)
        d = city_distance_km("São Paulo", "Rio de Janeiro")
        self.assertTrue(350 < d < 400, d)
        self.assertTrue(city_distance_km("São Paulo", "Tokyo") > 15000)
        self.assertTrue(all(v.country for v in CITIES.values()))


if __name__ == "__main__":
    unittest.main()
