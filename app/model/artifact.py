"""Artefato do modelo (formato nativo, SEM pickle) + adapter de serving.

Diretório do artefato:
    model.json | model.txt   # XGBoost (JSON nativo) ou LightGBM (texto nativo)
    meta.json                # versão, features ordenadas, threshold, métricas, referência p/ drift

`FraudModel.load(dir)` devolve um objeto com `predict` (scores 0-1) e `explain`
(top-N contribuições por feature, na escala de log-odds), independente de qual
biblioteca treinou o modelo. O serving importa só a biblioteca do artefato.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

MODEL_FILES = {"xgboost": "model.json", "lightgbm": "model.txt"}


def save_artifact(
    model_dir: Path, booster: Any, model_type: str, meta: dict, num_iteration: int | None = None
) -> None:
    """`num_iteration`: nº de árvores/rodadas a gravar (a melhor iteração do early
    stopping). Sem isso o arquivo levaria também as rodadas extras que o early
    stopping treinou depois do melhor ponto, e o modelo servido deixaria de ser o
    que foi avaliado (o threshold foi calibrado no modelo truncado)."""
    if model_type not in MODEL_FILES:
        raise ValueError(f"model_type inválido: {model_type}")
    model_dir.mkdir(parents=True, exist_ok=True)
    path = str(model_dir / MODEL_FILES[model_type])
    if model_type == "xgboost":
        (booster[:num_iteration] if num_iteration else booster).save_model(path)
    else:
        booster.save_model(path, num_iteration=num_iteration)
    (model_dir / "meta.json").write_text(json.dumps({**meta, "model_type": model_type}, indent=2, ensure_ascii=False))


class FraudModel:
    def __init__(self, booster: Any, model_type: str, meta: dict):
        self._booster = booster
        self.model_type = model_type
        self.meta = meta
        self.feature_names: list[str] = list(meta["feature_names"])
        self.threshold: float = float(meta["threshold"])
        self.version: str = meta["version"]

    @classmethod
    def load(cls, model_dir: Path | str) -> "FraudModel":
        model_dir = Path(model_dir)
        meta = json.loads((model_dir / "meta.json").read_text())
        model_type = meta["model_type"]
        path = str(model_dir / MODEL_FILES[model_type])
        if model_type == "xgboost":
            import xgboost as xgb

            booster = xgb.Booster()
            booster.load_model(path)
            booster.set_param({"nthread": 1})  # requisições pequenas: 1 thread (OpenMP com vários núcleos gasta CPU em espera ativa)
        else:
            import lightgbm as lgb

            booster = lgb.Booster(model_file=path)
        return cls(booster, model_type, meta)

    # ------------------------------------------------------------------ scoring
    def _check(self, X: np.ndarray) -> np.ndarray:
        X = np.asarray(X, dtype=np.float64)
        if X.ndim == 1:
            X = X.reshape(1, -1)
        if X.shape[1] != len(self.feature_names):
            raise ValueError(f"esperava {len(self.feature_names)} features, recebeu {X.shape[1]}")
        return X

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Score de risco em [0, 1] (não calibrado como probabilidade)."""
        X = self._check(X)
        if self.model_type == "xgboost":
            import xgboost as xgb

            return np.asarray(self._booster.predict(xgb.DMatrix(X, nthread=1)))
        return np.asarray(self._booster.predict(X, num_threads=1))

    def contributions(self, X: np.ndarray) -> np.ndarray:
        """(n, F+1): contribuição por feature em log-odds; a última coluna é o viés.
        sigmoid(soma da linha) == predict(X)."""
        X = self._check(X)
        if self.model_type == "xgboost":
            import xgboost as xgb

            return np.asarray(self._booster.predict(xgb.DMatrix(X, nthread=1), pred_contribs=True))
        return np.asarray(self._booster.predict(X, pred_contrib=True, num_threads=1))

    def explain(self, x: np.ndarray, top_n: int = 3) -> list[dict]:
        x = self._check(x)
        contrib = self.contributions(x)[0][:-1]
        order = np.argsort(-np.abs(contrib))[:top_n]
        return [
            {"feature": self.feature_names[i], "value": float(x[0, i]), "contribution": float(contrib[i])}
            for i in order
        ]

    def is_flagged(self, score: float) -> bool:
        return score >= self.threshold
