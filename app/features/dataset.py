"""Construção offline da matriz de features: reproduz o stream pela mesma
máquina de estados usada no serving (`FeatureEngine.process`)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np

from app.data.schema import Transaction
from app.features.engine import N_FEATURES, FeatureEngine


@dataclass
class FeatureDataset:
    X: np.ndarray  # (n, N_FEATURES) float64, na ordem de FEATURE_NAMES
    y: np.ndarray  # (n,) int8: 1 = fraude
    ts: np.ndarray  # (n,) epoch seconds — base do split temporal
    txn_ids: list[str]
    patterns: list[str | None]

    def __len__(self) -> int:
        return len(self.y)

    def subset(self, mask: np.ndarray) -> "FeatureDataset":
        idx = np.flatnonzero(mask)
        return FeatureDataset(
            self.X[idx],
            self.y[idx],
            self.ts[idx],
            [self.txn_ids[i] for i in idx],
            [self.patterns[i] for i in idx],
        )


def build_dataset(txns: Iterable[Transaction]) -> FeatureDataset:
    """As transações DEVEM chegar em ordem temporal (como no stream)."""
    engine = FeatureEngine()
    rows: list[list[float]] = []
    y: list[int] = []
    ts: list[float] = []
    ids: list[str] = []
    patterns: list[str | None] = []
    for t in txns:
        rows.append(engine.process(t))
        y.append(1 if t.is_fraud else 0)
        ts.append(t.epoch)
        ids.append(t.transaction_id)
        patterns.append(t.fraud_pattern)
    X = np.asarray(rows, dtype=np.float64).reshape(-1, N_FEATURES)
    return FeatureDataset(X, np.asarray(y, dtype=np.int8), np.asarray(ts), ids, patterns)
