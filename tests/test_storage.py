import time
import unittest
from datetime import datetime, timedelta, timezone

from app.data.schema import Transaction
from app.storage.db import ScoredTransaction
from tests.pg import make_db, pg_available

T0 = datetime(2026, 3, 1, 12, tzinfo=timezone.utc)


def rec(i, user="u1", dt=0.0, score=0.1, flagged=False, fraud=None):
    txn = Transaction(
        transaction_id=f"t{i}", user_id=user, amount=10.0 + i, country="BR", city="São Paulo", device_id="d1",
        merchant_id="m_000", merchant_category="groceries", timestamp=T0 + timedelta(seconds=dt), is_fraud=fraud,
    )
    return ScoredTransaction(txn, [1.0, 2.0, 3.0], score, flagged, 0.5, "v-test", latency_ms=12.5)


@unittest.skipUnless(pg_available(), "PostgreSQL de teste indisponível (docker compose ... up -d postgres)")
class StorageTest(unittest.TestCase):
    def setUp(self):
        self.db = make_db()
        self.addCleanup(self.db.drop_schema)

    def test_insert_and_read_back_with_all_fields(self):
        self.assertTrue(self.db.insert_scored(rec(1, score=0.93, flagged=True, fraud=True)))
        with self.db.pool.connection() as c:
            row = c.execute(
                "SELECT score, flagged, threshold, model_version, features, latency_ms, is_fraud, scored_at, event_time "
                "FROM transactions WHERE transaction_id='t1'"
            ).fetchone()
        self.assertEqual(row[:7], (0.93, True, 0.5, "v-test", [1.0, 2.0, 3.0], 12.5, True))
        self.assertIsNotNone(row[7])
        self.assertEqual(row[8], T0)

    def test_insert_is_idempotent(self):
        self.assertTrue(self.db.insert_scored(rec(1)))
        self.assertFalse(self.db.insert_scored(rec(1, score=0.99)))  # reentrega: ignorada
        self.assertEqual(self.db.count(), 1)
        with self.db.pool.connection() as c:
            self.assertEqual(c.execute("SELECT score FROM transactions").fetchone()[0], 0.1)

    def test_seq_is_increasing(self):
        for i in range(5):
            self.db.insert_scored(rec(i))
        with self.db.pool.connection() as c:
            seqs = [r[0] for r in c.execute("SELECT seq FROM transactions ORDER BY event_time, transaction_id").fetchall()]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(len(set(seqs)), 5)

    def test_user_history_is_chronological_limited_and_per_user(self):
        for i in range(6):
            self.db.insert_scored(rec(i, user="u1", dt=i * 60))
        self.db.insert_scored(rec(100, user="u2", dt=5))
        hist = self.db.load_user_history("u1", limit=4)
        self.assertEqual([t.transaction_id for t in hist], ["t2", "t3", "t4", "t5"])  # as 4 mais recentes, em ordem
        self.assertTrue(all(t.user_id == "u1" for t in hist))
        self.assertEqual(hist[0].timestamp, T0 + timedelta(seconds=120))
        self.assertEqual(self.db.load_user_history("ninguem"), [])

    def test_history_feeds_state_reconstruction(self):
        from app.features.engine import UserState, compute_features, update_state

        txs = [rec(i, dt=i * 30).txn for i in range(5)]
        for i in range(5):
            self.db.insert_scored(rec(i, dt=i * 30))
        live = UserState()
        for t in txs:
            update_state(live, t)
        rebuilt = UserState.from_transactions(self.db.load_user_history("u1"))
        nxt = rec(9, dt=200).txn
        self.assertEqual(compute_features(live, nxt), compute_features(rebuilt, nxt))

    def test_retention_removes_old_rows_and_keeps_recent(self):
        for i in range(4):
            self.db.insert_scored(rec(i))
        with self.db.pool.connection() as c:
            c.execute("UPDATE transactions SET scored_at = now() - interval '72 hours' WHERE transaction_id IN ('t0','t1')")
        removed = self.db.sweep_retention(hours=48, max_rows=1000)
        self.assertEqual(removed, 2)
        with self.db.pool.connection() as c:
            ids = {r[0] for r in c.execute("SELECT transaction_id FROM transactions").fetchall()}
        self.assertEqual(ids, {"t2", "t3"})

    def test_retention_caps_row_count_keeping_the_newest(self):
        for i in range(10):
            self.db.insert_scored(rec(i))
        self.db.sweep_retention(hours=48, max_rows=4)
        self.assertEqual(self.db.count(), 4)
        with self.db.pool.connection() as c:
            ids = {r[0] for r in c.execute("SELECT transaction_id FROM transactions").fetchall()}
        self.assertEqual(ids, {"t6", "t7", "t8", "t9"})

    def test_latency_and_model_version_are_aggregable(self):
        for i in range(10):
            r = rec(i)
            r.latency_ms = float(10 * (i + 1))
            self.db.insert_scored(r)
        with self.db.pool.connection() as c:
            p50, p95, n = c.execute(
                "SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms), "
                "percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms), count(*) FROM transactions"
            ).fetchone()
            by_version = c.execute("SELECT model_version, count(*) FROM transactions GROUP BY 1").fetchall()
        self.assertAlmostEqual(p50, 55.0)
        self.assertAlmostEqual(p95, 95.5)
        self.assertEqual(by_version, [("v-test", 10)])

    def test_ping_and_max_event_time(self):
        self.assertTrue(self.db.ping())
        self.assertIsNone(self.db.max_event_time())
        self.db.insert_scored(rec(1, dt=10))
        self.assertEqual(self.db.max_event_time(), T0 + timedelta(seconds=10))


if __name__ == "__main__":
    unittest.main()
