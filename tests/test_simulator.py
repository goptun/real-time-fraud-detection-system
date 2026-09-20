import collections
import statistics
import unittest
from datetime import datetime, timezone

from app.data.geo import CITIES, haversine_km
from app.data.schema import FRAUD_PATTERNS, Transaction
from app.data.simulator import SimConfig, Simulator

N_USERS, DAYS = 150, 30


def _fingerprint(txns):
    return [
        (t.transaction_id, t.user_id, t.amount, t.timestamp, t.city, t.device_id, t.merchant_id, t.is_fraud, t.fraud_pattern)
        for t in txns
    ]


def _speed_kmh(a: Transaction, b: Transaction) -> float:
    ca, cb = CITIES[a.city], CITIES[b.city]
    km = haversine_km(ca.lat, ca.lon, cb.lat, cb.lon)
    hours = max((b.timestamp - a.timestamp).total_seconds(), 1.0) / 3600.0
    return km / hours


class SimulatorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sim = Simulator(SimConfig(n_users=N_USERS, seed=11))
        cls.txns = cls.sim.advance(DAYS)
        cls.legit = [t for t in cls.txns if not t.is_fraud]
        cls.fraud = [t for t in cls.txns if t.is_fraud]
        cls.by_user = collections.defaultdict(list)
        for t in cls.txns:
            cls.by_user[t.user_id].append(t)

    # ---- 2.2 perfis e outliers legítimos
    def test_legit_transactions_respect_user_profile(self):
        users = {u.user_id: u for u in self.sim.users}
        at_home = sum(t.city == users[t.user_id].home_city for t in self.legit)
        self.assertGreater(at_home / len(self.legit), 0.85)

        # Merchants favoritos dominam (85% por construção, menos as compras grandes).
        from app.data.catalog import MERCHANTS

        ids = [m for m, _ in MERCHANTS]
        fav = sum(t.merchant_id in {ids[i] for i in users[t.user_id].favorites} for t in self.legit)
        self.assertGreater(fav / len(self.legit), 0.70)

        # A mediana de valor de cada usuário fica perto do seu perfil (exp(mu) ± deslocamento de categoria).
        import math

        checked = 0
        for uid, txs in self.by_user.items():
            amounts = [t.amount for t in txs if not t.is_fraud]
            if len(amounts) < 40:
                continue
            ratio = statistics.median(amounts) / math.exp(users[uid].mu)
            self.assertTrue(0.3 < ratio < 3.5, (uid, ratio))
            checked += 1
        self.assertGreater(checked, 50)

    def test_legit_outliers_exist_and_are_not_fraud(self):
        # compra grande legítima
        big = 0
        for txs in self.by_user.values():
            legit_amounts = [t.amount for t in txs if not t.is_fraud]
            if len(legit_amounts) < 20:
                continue
            med = statistics.median(legit_amounts)
            big += sum(a > 5 * med for a in legit_amounts)
        self.assertGreater(big, 20)

        # dispositivo novo legítimo (primeira aparição depois da 1ª transação do usuário)
        new_dev_legit = 0
        for txs in self.by_user.values():
            seen = {txs[0].device_id}
            for t in txs[1:]:
                if t.device_id not in seen and not t.is_fraud:
                    new_dev_legit += 1
                seen.add(t.device_id)
        self.assertGreater(new_dev_legit, 0)

        # viagem legítima: transações legítimas fora da cidade "casa"
        users = {u.user_id: u for u in self.sim.users}
        away = sum(t.city != users[t.user_id].home_city for t in self.legit)
        self.assertGreater(away, 0)

    def test_legit_travel_is_physically_plausible(self):
        # Entre duas transações legítimas consecutivas do mesmo usuário nunca há "teletransporte".
        for txs in self.by_user.values():
            legit = [t for t in txs if not t.is_fraud]
            for a, b in zip(legit, legit[1:]):
                self.assertLess(_speed_kmh(a, b), 800.0, (a.transaction_id, b.transaction_id))

    # ---- 2.3 padrões de fraude
    def test_fraud_rate_is_rare_and_close_to_target(self):
        rate = len(self.fraud) / len(self.txns)
        self.assertLess(rate, 0.05)
        self.assertAlmostEqual(rate, 0.02, delta=0.006)

    def test_every_pattern_present_and_none_explains_all(self):
        counts = collections.Counter(t.fraud_pattern for t in self.fraud)
        self.assertEqual(set(counts), set(FRAUD_PATTERNS))
        for pattern, n in counts.items():
            share = n / len(self.fraud)
            self.assertGreater(share, 0.04, pattern)
            self.assertLess(share, 0.6, pattern)

    def test_labels_are_consistent(self):
        self.assertTrue(all(t.fraud_pattern for t in self.fraud))
        self.assertTrue(all(t.fraud_pattern is None and t.is_fraud is False for t in self.legit))

    def test_velocity_frauds_come_in_bursts(self):
        by_user = collections.defaultdict(list)
        for t in self.fraud:
            if t.fraud_pattern == "velocity":
                by_user[t.user_id].append(t.timestamp)
        bursts = 0
        for stamps in by_user.values():
            stamps.sort()
            bursts += sum((b - a).total_seconds() < 120 for a, b in zip(stamps, stamps[1:]))
        self.assertGreater(bursts, 20)

    def test_impossible_travel_is_physically_impossible(self):
        speeds = []
        for txs in self.by_user.values():
            for prev, cur in zip(txs, txs[1:]):
                if cur.fraud_pattern == "impossible_travel" and not prev.is_fraud:
                    speeds.append(_speed_kmh(prev, cur))
        self.assertGreater(len(speeds), 10)
        self.assertGreater(sum(s > 1000 for s in speeds) / len(speeds), 0.9)

    # ---- 2.4 determinismo e ordem temporal
    def test_timestamps_sorted_and_ids_unique(self):
        stamps = [t.timestamp for t in self.txns]
        self.assertEqual(stamps, sorted(stamps))
        self.assertEqual(len({t.transaction_id for t in self.txns}), len(self.txns))

    def test_same_seed_same_dataset(self):
        again = Simulator(SimConfig(n_users=N_USERS, seed=11)).advance(DAYS)
        self.assertEqual(_fingerprint(again), _fingerprint(self.txns))

    def test_different_seed_different_dataset(self):
        other = Simulator(SimConfig(n_users=N_USERS, seed=12)).advance(DAYS)
        self.assertNotEqual(_fingerprint(other), _fingerprint(self.txns))

    def test_time_split_has_no_overlap(self):
        cut = self.txns[int(len(self.txns) * 0.8)].timestamp
        train = [t for t in self.txns if t.timestamp < cut]
        test = [t for t in self.txns if t.timestamp >= cut]
        self.assertTrue(train and test)
        self.assertLess(max(t.timestamp for t in train), min(t.timestamp for t in test))

    def test_generated_transactions_pass_schema_validation(self):
        for t in self.txns[:300]:
            Transaction.model_validate(t.model_dump())


class ContinuousReplayTest(unittest.TestCase):
    def test_consecutive_windows_continue_in_time_with_unique_ids(self):
        sim = Simulator(SimConfig(n_users=60, seed=5, id_prefix="r1"))
        first, second = sim.advance(3), sim.advance(3)
        self.assertLess(first[-1].timestamp, second[0].timestamp)
        ids = [t.transaction_id for t in first + second]
        self.assertEqual(len(set(ids)), len(ids))
        self.assertTrue(all(i.startswith("r1_") for i in ids))

    def test_start_in_the_middle_of_a_day_emits_nothing_earlier(self):
        start = datetime(2026, 5, 5, 13, 30, tzinfo=timezone.utc)
        txns = Simulator(SimConfig(n_users=60, seed=5, start=start)).advance(2)
        self.assertTrue(txns)
        self.assertGreaterEqual(min(t.timestamp for t in txns), start)


if __name__ == "__main__":
    unittest.main()
