import collections
import math
import unittest
from datetime import datetime, timezone

import numpy as np

from app.data.geo import city_distance_km
from app.data.schema import Transaction
from app.data.simulator import SimConfig, Simulator
from app.features.dataset import build_dataset
from app.features.engine import (
    FEATURE_NAMES,
    LOG_MAX,
    MAX_GAP_SECONDS,
    MAX_SPEED_KMH,
    FeatureEngine,
    UserState,
    UserStateStore,
    compute_features,
    features_as_dict,
    update_state,
)

T0 = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc).timestamp()


def mk(i, dt, user="u1", amount=100.0, city="São Paulo", country=None, device="d1", merchant="m_000", category="groceries"):
    from app.data.geo import CITIES

    return Transaction(
        transaction_id=f"t{i}",
        user_id=user,
        amount=amount,
        country=country or CITIES[city].country,
        city=city,
        device_id=device,
        merchant_id=merchant,
        merchant_category=category,
        timestamp=datetime.fromtimestamp(T0 + dt, tz=timezone.utc),
    )


def feats(state, txn):
    return features_as_dict(compute_features(state, txn))


class FeatureUnitTest(unittest.TestCase):
    def test_first_transaction_uses_documented_defaults(self):
        f = feats(UserState(), mk(1, 0, amount=250.0))
        self.assertEqual(f["amount"], 250.0)
        self.assertEqual(f["amount_zscore_user"], 0.0)
        self.assertEqual(f["amount_ratio_to_user_max"], 1.0)
        for k in ("txn_count_1m", "txn_count_10m", "txn_count_1h", "txn_count_24h", "txn_count_30d"):
            self.assertEqual(f[k], 0.0)
        self.assertEqual(f["seconds_since_last"], MAX_GAP_SECONDS)
        for k in ("is_new_device", "is_new_city", "is_new_country", "is_new_merchant"):
            self.assertEqual(f[k], 0.0, k)
        self.assertEqual((f["km_from_last"], f["implied_speed_kmh"]), (0.0, 0.0))

    def test_vector_matches_feature_names(self):
        self.assertEqual(len(compute_features(UserState(), mk(1, 0))), len(FEATURE_NAMES))

    def test_velocity_windows_and_boundaries(self):
        st = UserState()
        for i, dt in enumerate((0, 10, 20, 30, 40)):
            update_state(st, mk(i, dt))
        f = feats(st, mk(9, 50))
        self.assertEqual([f[k] for k in ("txn_count_1m", "txn_count_10m", "txn_count_1h", "txn_count_24h")], [5, 5, 5, 5])
        # +100s: só o evento de +40s (idade exata 60s, borda inclusiva) cai em 1 min
        f = feats(st, mk(9, 100))
        self.assertEqual((f["txn_count_1m"], f["txn_count_10m"]), (1.0, 5.0))
        # +2h: nada em 1 min/10 min/1 h, tudo em 24 h
        f = feats(st, mk(9, 7200))
        self.assertEqual([f[k] for k in ("txn_count_1m", "txn_count_10m", "txn_count_1h", "txn_count_24h")], [0, 0, 0, 5])
        # +25h: fora até de 24 h
        f = feats(st, mk(9, 25 * 3600))
        self.assertEqual(f["txn_count_24h"], 0.0)

    def test_velocity_feature_grows_with_a_burst(self):
        eng = FeatureEngine()
        counts = [eng.process(mk(i, i * 5.0))[FEATURE_NAMES.index("txn_count_1m")] for i in range(8)]
        self.assertEqual(counts, [0, 1, 2, 3, 4, 5, 6, 7])

    def test_seconds_since_last(self):
        st = UserState()
        update_state(st, mk(1, 0))
        self.assertEqual(feats(st, mk(2, 90))["seconds_since_last"], 90.0)

    def test_new_device_city_country_merchant_flags(self):
        st = UserState()
        update_state(st, mk(1, 0))
        same = feats(st, mk(2, 60))
        self.assertEqual([same[k] for k in ("is_new_device", "is_new_city", "is_new_country", "is_new_merchant")], [0, 0, 0, 0])
        f = feats(st, mk(3, 60, device="d2", city="Rio de Janeiro", merchant="m_001"))
        self.assertEqual([f[k] for k in ("is_new_device", "is_new_city", "is_new_country", "is_new_merchant")], [1, 1, 0, 1])
        f = feats(st, mk(4, 60, city="Lisboa"))
        self.assertEqual((f["is_new_city"], f["is_new_country"]), (1.0, 1.0))
        update_state(st, mk(5, 30, device="d2"))
        self.assertEqual(feats(st, mk(6, 60, device="d2"))["is_new_device"], 0.0)

    def test_distance_and_implied_speed(self):
        st = UserState()
        update_state(st, mk(1, 0))
        km = city_distance_km("São Paulo", "Rio de Janeiro")
        f = feats(st, mk(2, 3600, city="Rio de Janeiro"))
        self.assertAlmostEqual(f["km_from_last"], km, places=6)
        self.assertAlmostEqual(f["implied_speed_kmh"], km, places=6)  # 1 h
        # quase simultâneo em outro continente: velocidade limitada (não infinita)
        f = feats(st, mk(3, 10, city="Tokyo"))
        self.assertEqual(f["implied_speed_kmh"], MAX_SPEED_KMH)

    def test_amount_zscore_and_ratio_to_max(self):
        st = UserState()
        for i, a in enumerate((90, 100, 110, 95, 105)):
            update_state(st, mk(i, i * 60.0, amount=float(a)))
        typical = feats(st, mk(9, 600, amount=100.0))
        spike = feats(st, mk(9, 600, amount=1000.0))
        self.assertLess(abs(typical["amount_zscore_user"]), 1.0)
        self.assertGreater(spike["amount_zscore_user"], 5.0)
        self.assertAlmostEqual(spike["amount_ratio_to_user_max"], 1000.0 / 110.0)

    def test_category_and_hour_features(self):
        f = feats(UserState(), mk(1, 0, category="crypto_exchange"))
        self.assertEqual(f["is_risky_category"], 1.0)
        self.assertEqual(f["hour_of_day"], 12.0)
        self.assertEqual(feats(UserState(), mk(2, 0, category="groceries"))["is_risky_category"], 0.0)

    def test_users_are_independent(self):
        eng = FeatureEngine()
        eng.process(mk(1, 0, user="a"))
        f = features_as_dict(eng.process(mk(2, 30, user="b")))
        self.assertEqual(f["txn_count_30d"], 0.0)
        self.assertEqual(f["txn_count_1m"], 0.0)


class LeakageAndOrderingTest(unittest.TestCase):
    def test_late_event_ignores_the_future(self):
        a = mk(1, 0, device="d1", city="São Paulo")
        late = mk(2, 100, device="d2", city="Rio de Janeiro", amount=100.0)
        future = mk(3, 200, device="d2", city="Rio de Janeiro", amount=5000.0)

        with_future = UserState()
        update_state(with_future, a)
        update_state(with_future, future)  # já processou um evento POSTERIOR ao `late`

        only_past = UserState()
        update_state(only_past, a)

        self.assertEqual(compute_features(with_future, late), compute_features(only_past, late))
        # e o vazamento seria detectável: d2/Rio já existem no state "com futuro"
        self.assertEqual(feats(with_future, late)["is_new_device"], 1.0)
        self.assertEqual(feats(with_future, late)["txn_count_1h"], 1.0)

    def test_compute_features_does_not_mutate_state(self):
        st = UserState()
        update_state(st, mk(1, 0))
        before = (st.n, list(st.log), dict(st.devices))
        compute_features(st, mk(2, 60, device="dX"))
        self.assertEqual(before, (st.n, list(st.log), dict(st.devices)))

    def test_duplicate_transaction_id_does_not_change_state(self):
        st = UserState()
        t = mk(1, 0, amount=80.0)
        self.assertTrue(update_state(st, t))
        snapshot = (st.n, st.mean_ln, st.m2_ln, dict(st.buckets), list(st.log))
        self.assertFalse(update_state(st, t))
        self.assertEqual(snapshot, (st.n, st.mean_ln, st.m2_ln, dict(st.buckets), list(st.log)))
        nxt = mk(2, 30)
        fresh = UserState()
        update_state(fresh, t)
        self.assertEqual(compute_features(st, nxt), compute_features(fresh, nxt))

    def test_late_event_is_inserted_in_chronological_order_and_counts_for_the_future(self):
        st = UserState()
        update_state(st, mk(1, 0))
        update_state(st, mk(3, 200))
        update_state(st, mk(2, 100))  # chegou fora de ordem
        self.assertEqual([e.ts for e in st.log], [T0, T0 + 100, T0 + 200])
        self.assertEqual(st.n, 3)
        self.assertEqual(st.last_ts, T0 + 200)

    def test_log_is_bounded_but_history_keeps_counting(self):
        st = UserState()
        for i in range(LOG_MAX * 2):
            update_state(st, mk(i, i * 0.5))
        self.assertLessEqual(len(st.log), LOG_MAX)
        self.assertEqual(st.n, LOG_MAX * 2)


DAY = 86400.0


class RollingMemoryTest(unittest.TestCase):
    def test_entities_unseen_for_30_days_are_new_again(self):
        st = UserState()
        update_state(st, mk(1, 0, device="old", merchant="m_001", city="Rio de Janeiro"))
        update_state(st, mk(2, 40 * DAY, device="cur"))  # usuário voltou 40 dias depois
        # "old"/Rio/m_001 foram vistos há 40 dias: fora da memória → novos de novo
        f = feats(st, mk(3, 40 * DAY + 60, device="old", merchant="m_001", city="Rio de Janeiro"))
        self.assertEqual((f["is_new_device"], f["is_new_city"], f["is_new_merchant"]), (1.0, 1.0, 1.0))
        # visto há 10 dias: ainda lembrado
        st2 = UserState()
        update_state(st2, mk(1, 0, device="old"))
        update_state(st2, mk(2, 10 * DAY, device="cur"))
        self.assertEqual(feats(st2, mk(3, 10 * DAY + 60, device="old"))["is_new_device"], 0.0)

    def test_count_and_max_use_only_the_30_day_window(self):
        st = UserState()
        update_state(st, mk(1, 0, amount=9000.0))  # compra enorme, fora da janela quando chegar a próxima
        for i in range(5):
            update_state(st, mk(10 + i, 45 * DAY + i * 60, amount=100.0))
        f = feats(st, mk(99, 45 * DAY + 600, amount=200.0))
        self.assertEqual(f["txn_count_30d"], 5.0)
        self.assertAlmostEqual(f["amount_ratio_to_user_max"], 2.0)  # 200/100, não 200/9000

    def test_dormant_user_is_treated_as_cold_start(self):
        st = UserState()
        update_state(st, mk(1, 0))
        f = feats(st, mk(2, 60 * DAY, device="dX"))
        self.assertEqual((f["txn_count_30d"], f["is_new_device"], f["amount_ratio_to_user_max"]), (0.0, 0.0, 1.0))

    def test_state_size_is_bounded_over_a_long_history(self):
        st = UserState()
        for d in range(200):  # 200 dias, um merchant/dispositivo/cidade novo por dia
            update_state(st, mk(d, d * DAY, device=f"d{d}", merchant=f"m{d}", city=("São Paulo", "Rio de Janeiro")[d % 2]))
        self.assertLessEqual(len(st.buckets), 31)
        self.assertLessEqual(len(st.devices), 32)
        self.assertLessEqual(len(st.merchants), 32)
        self.assertEqual(st.n, 200)  # só a média/variância do valor guarda todo o histórico


class StationarityTest(unittest.TestCase):
    def test_history_dependent_features_do_not_drift_with_time(self):
        """Regressão: com memória acumulada, PSI de user_txn_count passava de 0.3 em 12 dias e a taxa
        de is_new_merchant caía de 14.7% para 4.7% em um ano — alerta falso de drift e modelo degradando."""
        from app.model.drift import make_reference_column, psi_column

        ds = build_dataset(Simulator(SimConfig(n_users=120, seed=8)).advance(130))
        day = (ds.ts - ds.ts[0]) / DAY
        ref, late = (day >= 40) & (day < 52), (day >= 118) & (day < 130)
        for name in ("txn_count_30d", "is_new_merchant", "amount_ratio_to_user_max", "is_new_device", "amount_zscore_user"):
            col = FEATURE_NAMES.index(name)
            psi = psi_column(make_reference_column(ds.X[ref, col]), ds.X[late, col])
            self.assertLess(psi, 0.1, name)


def _txns(n_users=30, days=15, seed=3):
    return Simulator(SimConfig(n_users=n_users, seed=seed)).advance(days)


class ParityAndReconstructionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.txns = _txns()
        cls.offline = build_dataset(cls.txns)

    def test_offline_and_streaming_paths_are_identical(self):
        # "Streaming": compute → (persistir) → update, com store LRU minúsculo
        # que reconstrói o state do "banco" a cada evicção.
        db = collections.defaultdict(list)
        store = UserStateStore(capacity=4, loader=lambda uid: list(db[uid]))
        vectors = []
        for t in self.txns:
            st = store.get(t.user_id)
            vectors.append(compute_features(st, t))
            self.assertTrue(update_state(st, t))
            db[t.user_id].append(t)
        self.assertGreater(store.loads, 100)  # houve reconstrução de verdade
        self.assertLessEqual(len(store), 4)
        np.testing.assert_array_equal(np.asarray(vectors), self.offline.X)

    def test_reconstructed_state_gives_same_features_as_continuous_state(self):
        by_user = collections.defaultdict(list)
        for t in self.txns:
            by_user[t.user_id].append(t)
        uid, history = max(by_user.items(), key=lambda kv: len(kv[1]))
        half = len(history) // 2
        continuous = UserState()
        for t in history[:half]:
            update_state(continuous, t)
        rebuilt = UserState.from_transactions(reversed(history[:half]))  # ordem embaralhada de propósito
        for t in history[half:]:
            self.assertEqual(compute_features(continuous, t), compute_features(rebuilt, t))
            update_state(continuous, t)
            update_state(rebuilt, t)

    def test_matches_an_independent_brute_force_reference(self):
        by_user = collections.defaultdict(list)
        for t in self.txns:
            by_user[t.user_id].append(t)
        index = {t.transaction_id: i for i, t in enumerate(self.txns)}
        names = {n: k for k, n in enumerate(FEATURE_NAMES)}
        checked = 0
        for txs in by_user.values():
            for j, t in enumerate(txs):
                if j % 3:
                    continue
                prior = txs[:j]
                row = self.offline.X[index[t.transaction_id]]
                tt = t.epoch
                for w, name in ((60, "txn_count_1m"), (600, "txn_count_10m"), (3600, "txn_count_1h"), (86400, "txn_count_24h")):
                    self.assertEqual(row[names[name]], sum(tt - p.epoch <= w for p in prior), name)
                self.assertEqual(row[names["txn_count_30d"]], len(prior))
                if prior:
                    self.assertEqual(row[names["is_new_device"]], float(t.device_id not in {p.device_id for p in prior}))
                    self.assertEqual(row[names["is_new_city"]], float(t.city not in {p.city for p in prior}))
                    self.assertEqual(row[names["is_new_merchant"]], float(t.merchant_id not in {p.merchant_id for p in prior}))
                    self.assertAlmostEqual(row[names["seconds_since_last"]], min(tt - prior[-1].epoch, MAX_GAP_SECONDS))
                    self.assertAlmostEqual(row[names["km_from_last"]], city_distance_km(prior[-1].city, t.city))
                    ratio = min(t.amount / max(p.amount for p in prior), 50.0)
                    self.assertAlmostEqual(row[names["amount_ratio_to_user_max"]], ratio)
                checked += 1
        self.assertGreater(checked, 500)

    def test_dataset_shape_labels_and_no_nans(self):
        ds = self.offline
        self.assertEqual(ds.X.shape, (len(self.txns), len(FEATURE_NAMES)))
        self.assertEqual(int(ds.y.sum()), sum(bool(t.is_fraud) for t in self.txns))
        self.assertFalse(np.isnan(ds.X).any())
        self.assertTrue(np.all(np.diff(ds.ts) >= 0))

    def test_fraud_patterns_move_the_intended_features(self):
        ds, f = self.offline, {n: k for k, n in enumerate(FEATURE_NAMES)}
        pats = np.array([p or "" for p in ds.patterns])
        legit = ds.y == 0
        velocity = pats == "velocity"
        travel = pats == "impossible_travel"
        self.assertGreater(ds.X[velocity, f["txn_count_10m"]].mean(), 3 * ds.X[legit, f["txn_count_10m"]].mean())
        self.assertGreater(ds.X[travel, f["implied_speed_kmh"]].mean(), 10 * ds.X[legit, f["implied_speed_kmh"]].mean())


if __name__ == "__main__":
    unittest.main()
