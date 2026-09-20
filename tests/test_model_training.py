import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from app.features.engine import FEATURE_NAMES
from app.model.artifact import FraudModel
from app.model.training import TrainConfig, build_splits, run_training

# early_stopping_rounds baixo de propósito: garante que o early stopping dispara (regressão: o artefato
# salvo tinha as árvores extras além de best_iteration e divergia do modelo avaliado).
SMALL = TrainConfig(users=250, days=30, seed=5, n_estimators=400, early_stopping_rounds=3, n_jobs=2)


class TrainingPipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.model_dir = Path(cls.tmp.name) / "models"
        cls.reports = Path(cls.tmp.name) / "reports"
        cls.result = run_training(SMALL, cls.model_dir, cls.reports, log=lambda *_: None)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_all_candidates_evaluated_on_same_test_set_with_required_metrics(self):
        c = self.result["candidates"]
        self.assertEqual(
            set(c),
            {"baseline_regra_valor", "logistic_regression", "xgboost_plain", "xgboost_weighted", "lightgbm_plain", "lightgbm_weighted"},
        )
        for name, r in c.items():
            for k in ("precision", "recall", "f1", "fpr"):
                self.assertIn(k, r["test"], name)
            self.assertGreater(r["test_pr_auc"], 0.0)
            # mesmo conjunto de teste para todos: mesma taxa de fraude
            self.assertEqual(r["test_fraud_rate"], self.result["test_fraud_rate"])

    def test_gradient_boosting_beats_the_naive_rule(self):
        c = self.result["candidates"]
        self.assertGreater(c[self.result["selected"]]["test_pr_auc"], c["baseline_regra_valor"]["test_pr_auc"] + 0.1)

    def test_selection_uses_validation_only_and_serves_a_gbm(self):
        c = self.result["candidates"]
        gbms = {n: v for n, v in c.items() if v["family"] in ("xgboost", "lightgbm")}
        self.assertEqual(self.result["selected"], max(gbms, key=lambda n: gbms[n]["val_pr_auc"]))

    def test_threshold_respects_the_fpr_budget_on_validation(self):
        sel = self.result["candidates"][self.result["selected"]]
        self.assertLessEqual(sel["val"]["fpr"], SMALL.max_fpr + 1e-12)

    def test_artifact_files_and_metadata(self):
        self.assertTrue((self.model_dir / "meta.json").exists())
        meta = json.loads((self.model_dir / "meta.json").read_text())
        self.assertEqual(meta["feature_names"], list(FEATURE_NAMES))
        for key in ("version", "threshold", "metrics", "comparison", "reference", "trained_at", "model_type"):
            self.assertIn(key, meta)
        self.assertTrue(meta["data"]["synthetic"])
        # estatísticas de referência: por feature e do score (p/ drift)
        self.assertEqual(set(meta["reference"]["features"]), set(FEATURE_NAMES))
        self.assertIn("score", meta["reference"])

    def test_reports_written(self):
        for f in ("evaluation.json", "evaluation.md"):
            self.assertTrue((self.reports / f).exists(), f)
        self.assertIn("| modelo |", (self.reports / "evaluation.md").read_text())

    def test_artifact_reloads_and_reproduces_scores_on_the_test_set(self):
        model = FraudModel.load(self.model_dir)
        _, (_, _, te) = build_splits(SMALL)
        scores = model.predict(te.X)
        meta = json.loads((self.model_dir / "meta.json").read_text())
        from app.model.metrics import metrics_at_threshold, pr_auc

        self.assertAlmostEqual(pr_auc(te.y, scores), meta["metrics"]["test_pr_auc"], places=9)
        again = metrics_at_threshold(te.y, scores, model.threshold)
        self.assertEqual((again["tp"], again["fp"]), (meta["metrics"]["test"]["tp"], meta["metrics"]["test"]["fp"]))
        self.assertTrue(np.all((scores >= 0) & (scores <= 1)))
        self.assertEqual(model.version, meta["version"])

    def test_reproducible_by_seed(self):
        with tempfile.TemporaryDirectory() as d:
            again = run_training(SMALL, Path(d) / "m", None, log=lambda *_: None)
        for name, c in self.result["candidates"].items():
            self.assertAlmostEqual(c["test_pr_auc"], again["candidates"][name]["test_pr_auc"], delta=1e-6, msg=name)


if __name__ == "__main__":
    unittest.main()
