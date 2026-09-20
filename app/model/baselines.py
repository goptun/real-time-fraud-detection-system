"""Baselines de comparação: regra de valor e regressão logística."""

from __future__ import annotations

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import FunctionTransformer, StandardScaler

from app.features.engine import FEATURE_NAMES, feature_index

# Features assimétricas/heavy-tailed: log1p antes de padronizar (só o LR precisa disso).
_LOG_COLS = [
    feature_index(n)
    for n in (
        "amount", "amount_ratio_to_user_max", "txn_count_1m", "txn_count_10m", "txn_count_1h",
        "txn_count_24h", "seconds_since_last", "km_from_last", "implied_speed_kmh", "txn_count_30d",
    )
]


def _log_transform(X: np.ndarray) -> np.ndarray:
    X = np.array(X, dtype=float, copy=True)
    X[:, _LOG_COLS] = np.log1p(np.clip(X[:, _LOG_COLS], 0, None))
    return X


def amount_rule_scorer():
    """A regra ingênua de todo sistema antigo: 'valor alto = suspeito'."""
    col = FEATURE_NAMES.index("amount")
    return lambda X: np.asarray(X)[:, col].astype(float)


def fit_logistic_regression(X: np.ndarray, y: np.ndarray, seed: int):
    model = make_pipeline(
        FunctionTransformer(_log_transform),
        StandardScaler(),
        LogisticRegression(max_iter=500, random_state=seed),
    )
    model.fit(X, y)
    return lambda X_: model.predict_proba(X_)[:, 1]
