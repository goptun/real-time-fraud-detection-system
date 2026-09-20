import unittest
from datetime import datetime, timezone

import numpy as np

from app.data.simulator import SimConfig, Simulator
from app.features.dataset import build_dataset
from app.model.drift import compute_drift, make_reference, psi_column, make_reference_column
from app.model.metrics import evaluate_candidate, metrics_at_threshold, pr_auc, select_threshold
from app.model.split import temporal_split


class TemporalSplitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = build_dataset(Simulator(SimConfig(n_users=60, seed=2)).advance(20))

    def test_train_before_val_before_test_strictly(self):
        tr, va, te = temporal_split(self.ds)
        self.assertLess(tr.ts.max(), va.ts.min())
        self.assertLess(va.ts.max(), te.ts.min())
        self.assertEqual(len(tr) + len(va) + len(te), len(self.ds))

    def test_fractions_are_respected_approximately(self):
        tr, va, te = temporal_split(self.ds, (0.6, 0.2, 0.2))
        n = len(self.ds)
        self.assertAlmostEqual(len(tr) / n, 0.6, delta=0.03)
        self.assertAlmostEqual(len(va) / n, 0.2, delta=0.03)

    def test_rejects_bad_fractions_and_unsorted_input(self):
        with self.assertRaises(ValueError):
            temporal_split(self.ds, (0.5, 0.2, 0.2))
        shuffled = self.ds.subset(np.ones(len(self.ds), dtype=bool))
        shuffled.ts = shuffled.ts[::-1].copy()
        with self.assertRaises(ValueError):
            temporal_split(shuffled)


def _toy(n_neg=5000, n_pos=150, seed=0):
    rng = np.random.default_rng(seed)
    y = np.r_[np.zeros(n_neg, int), np.ones(n_pos, int)]
    s = np.r_[rng.beta(1, 8, n_neg), rng.beta(5, 3, n_pos)]
    return y, s


class MetricsTest(unittest.TestCase):
    def test_confusion_metrics_by_hand(self):
        y = np.array([1, 1, 1, 0, 0, 0, 0, 0])
        s = np.array([0.9, 0.8, 0.2, 0.7, 0.1, 0.1, 0.1, 0.1])
        m = metrics_at_threshold(y, s, 0.5)
        self.assertEqual((m["tp"], m["fp"], m["fn"], m["tn"]), (2, 1, 1, 4))
        self.assertAlmostEqual(m["precision"], 2 / 3)
        self.assertAlmostEqual(m["recall"], 2 / 3)
        self.assertAlmostEqual(m["f1"], 2 / 3)
        self.assertAlmostEqual(m["fpr"], 1 / 5)

    def test_pr_auc_perfect_and_random(self):
        y, _ = _toy()
        self.assertAlmostEqual(pr_auc(y, y.astype(float)), 1.0)
        rng = np.random.default_rng(1)
        self.assertLess(pr_auc(y, rng.random(len(y))), 0.1)

    def test_threshold_is_best_recall_within_fpr_budget(self):
        y, s = _toy()
        for max_fpr in (0.005, 0.01, 0.05):
            t = select_threshold(y, s, max_fpr)
            chosen = metrics_at_threshold(y, s, t)
            self.assertLessEqual(chosen["fpr"], max_fpr)
            # nenhum outro corte viável tem recall maior
            best = max(
                metrics_at_threshold(y, s, c)["recall"]
                for c in np.unique(s)
                if metrics_at_threshold(y, s, c)["fpr"] <= max_fpr
            )
            self.assertAlmostEqual(chosen["recall"], best)

    def test_threshold_needs_both_classes(self):
        with self.assertRaises(ValueError):
            select_threshold(np.zeros(10, int), np.random.rand(10))

    def test_test_set_never_influences_the_threshold(self):
        yv, sv = _toy(seed=1)
        yt, st = _toy(seed=2)
        a = evaluate_candidate(yv, sv, yt, st, 0.01)
        # troca completamente o teste (labels invertidos, scores embaralhados): o threshold não muda
        b = evaluate_candidate(yv, sv, 1 - yt, st[::-1].copy(), 0.01)
        self.assertEqual(a["threshold"], b["threshold"])
        self.assertNotEqual(a["test"]["recall"], b["test"]["recall"])

    def test_reports_required_metrics(self):
        yv, sv = _toy(seed=1)
        yt, st = _toy(seed=2)
        r = evaluate_candidate(yv, sv, yt, st, 0.01)
        for k in ("precision", "recall", "f1", "fpr"):
            self.assertIn(k, r["test"])
        self.assertIn("test_pr_auc", r)
        self.assertAlmostEqual(r["test_fraud_rate"], 150 / 5150)


class DriftTest(unittest.TestCase):
    def test_psi_low_for_same_distribution_and_high_when_shifted(self):
        rng = np.random.default_rng(0)
        ref = make_reference_column(rng.lognormal(4, 0.8, 50_000))
        same = psi_column(ref, rng.lognormal(4, 0.8, 2_000))
        shifted = psi_column(ref, rng.lognormal(4, 0.8, 2_000) * 3)
        self.assertLess(same, 0.05)
        self.assertGreater(shifted, 0.2)

    def test_binary_and_constant_features(self):
        rng = np.random.default_rng(1)
        ref = make_reference_column((rng.random(20_000) < 0.02).astype(float))
        self.assertLess(psi_column(ref, (rng.random(2_000) < 0.02).astype(float)), 0.05)
        self.assertGreater(psi_column(ref, (rng.random(2_000) < 0.3).astype(float)), 0.2)
        const = make_reference_column(np.zeros(1000))
        self.assertEqual(psi_column(const, np.zeros(50)), 0.0)

    def test_compute_drift_flags_only_the_shifted_feature(self):
        rng = np.random.default_rng(2)
        X = rng.lognormal(3, 0.5, (30_000, 3))
        names = ["a", "b", "c"]
        ref = make_reference(X, names, rng.random(30_000))
        window = rng.lognormal(3, 0.5, (2_000, 3))
        window[:, 1] *= 4
        d = compute_drift(ref, window, names, rng.random(2_000))
        self.assertGreater(d["features"]["b"], 0.2)
        self.assertLess(d["features"]["a"], 0.05)
        self.assertLess(d["score"], 0.05)


if __name__ == "__main__":
    unittest.main()
