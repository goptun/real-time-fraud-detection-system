import tempfile
import unittest
from pathlib import Path

import numpy as np

from app.model.artifact import FraudModel, save_artifact

NAMES = ["f_signal", "f_noise", "f_weak"]


def _toy_data(seed=0, n=4000):
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, 3))
    logit = 3.0 * X[:, 0] + 0.5 * X[:, 2] - 3.0
    y = (rng.random(n) < 1 / (1 + np.exp(-logit))).astype(int)
    return X, y


def _train(family):
    X, y = _toy_data()
    if family == "xgboost":
        import xgboost as xgb

        clf = xgb.XGBClassifier(n_estimators=40, max_depth=3, random_state=0, n_jobs=1).fit(X, y)
        return clf.get_booster(), clf.predict_proba(X)[:, 1], X
    import lightgbm as lgb

    clf = lgb.LGBMClassifier(n_estimators=40, num_leaves=8, random_state=0, n_jobs=1, verbose=-1).fit(X, y)
    return clf.booster_, clf.predict_proba(X)[:, 1], X


class ArtifactAdapterTest(unittest.TestCase):
    def _roundtrip(self, family):
        booster, expected, X = _train(family)
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        meta = {"version": "test-1", "feature_names": NAMES, "threshold": 0.42}
        save_artifact(Path(d.name), booster, family, meta)
        return FraudModel.load(d.name), expected, X

    def test_both_families_reload_and_reproduce_scores(self):
        for family in ("xgboost", "lightgbm"):
            with self.subTest(family):
                model, expected, X = self._roundtrip(family)
                np.testing.assert_allclose(model.predict(X), expected, atol=1e-6)
                self.assertTrue(np.all((model.predict(X) >= 0) & (model.predict(X) <= 1)))

    def test_exposes_version_threshold_and_features(self):
        for family in ("xgboost", "lightgbm"):
            with self.subTest(family):
                model, _, _ = self._roundtrip(family)
                self.assertEqual((model.version, model.threshold, model.feature_names, model.model_type), ("test-1", 0.42, NAMES, family))
                self.assertTrue(model.is_flagged(0.42))
                self.assertFalse(model.is_flagged(0.41))

    def test_contributions_are_consistent_with_the_score(self):
        for family in ("xgboost", "lightgbm"):
            with self.subTest(family):
                model, _, X = self._roundtrip(family)
                contrib = model.contributions(X[:200])
                self.assertEqual(contrib.shape, (200, 4))  # 3 features + viés
                margin = contrib.sum(axis=1)
                np.testing.assert_allclose(1 / (1 + np.exp(-margin)), model.predict(X[:200]), atol=1e-5)

    def test_explain_ranks_the_informative_feature_first(self):
        for family in ("xgboost", "lightgbm"):
            with self.subTest(family):
                model, _, X = self._roundtrip(family)
                row = np.array([[3.0, 0.0, 0.0]])  # f_signal muito alto
                top = model.explain(row, top_n=2)
                self.assertEqual(len(top), 2)
                self.assertEqual(top[0]["feature"], "f_signal")
                self.assertGreater(top[0]["contribution"], 0)
                self.assertEqual(top[0]["value"], 3.0)
                self.assertGreaterEqual(abs(top[0]["contribution"]), abs(top[1]["contribution"]))

    def test_artifact_keeps_only_the_requested_iterations(self):
        # Regressão: sem truncar em best_iteration o artefato levava as rodadas extras do early stopping.
        for family in ("xgboost", "lightgbm"):
            with self.subTest(family):
                booster, full_scores, X = _train(family)
                with tempfile.TemporaryDirectory() as d:
                    save_artifact(Path(d), booster, family, {"version": "t", "feature_names": NAMES, "threshold": 0.5}, num_iteration=10)
                    model = FraudModel.load(d)
                if family == "xgboost":
                    import xgboost as xgb

                    expected = booster.predict(xgb.DMatrix(X), iteration_range=(0, 10))
                else:
                    expected = booster.predict(X, num_iteration=10)
                np.testing.assert_allclose(model.predict(X), expected, atol=1e-6)
                self.assertGreater(np.abs(model.predict(X) - full_scores).max(), 1e-3)  # e difere do modelo completo

    def test_wrong_feature_count_is_rejected(self):
        model, _, _ = self._roundtrip("xgboost")
        with self.assertRaises(ValueError):
            model.predict(np.zeros((1, 5)))

    def test_invalid_model_type_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                save_artifact(Path(d), object(), "pickle", {})


if __name__ == "__main__":
    unittest.main()
