"""Drift por PSI (Population Stability Index) contra a distribuição de referência
do treino. Só NumPy: roda no serving (API) sem sklearn.

PSI = Σ (a_i − e_i) · ln(a_i / e_i)  sobre bins fixos definidos na referência.
Regra de bolso: < 0.1 estável; 0.1-0.2 atenção; > 0.2 deslocamento relevante.
"""

from __future__ import annotations

import numpy as np

_EPS = 1e-4


def _proportions(values: np.ndarray, edges: np.ndarray) -> np.ndarray:
    idx = np.searchsorted(edges, values, side="left")  # valor == borda cai no bin de baixo
    counts = np.bincount(idx, minlength=len(edges) + 1).astype(float)
    return counts / max(counts.sum(), 1.0)


def make_reference_column(values: np.ndarray, n_bins: int = 10) -> dict:
    """Bins por quantis (deduplicados: features binárias/discretas viram poucos bins)."""
    qs = np.quantile(values, np.linspace(0, 1, n_bins + 1)[1:-1])
    edges = np.unique(qs)
    return {"edges": edges.tolist(), "proportions": _proportions(values, edges).tolist()}


def make_reference(X: np.ndarray, feature_names: tuple[str, ...] | list[str], scores: np.ndarray) -> dict:
    return {
        "features": {name: make_reference_column(X[:, i]) for i, name in enumerate(feature_names)},
        "score": make_reference_column(scores),
    }


def psi_column(ref: dict, values: np.ndarray) -> float:
    if len(values) == 0:
        return 0.0
    e = np.clip(np.asarray(ref["proportions"], dtype=float), _EPS, None)
    a = np.clip(_proportions(values, np.asarray(ref["edges"], dtype=float)), _EPS, None)
    return float(np.sum((a - e) * np.log(a / e)))


def compute_drift(reference: dict, X: np.ndarray, feature_names, scores: np.ndarray) -> dict:
    """PSI por feature e do score para uma janela recente (X: n × F)."""
    return {
        "features": {name: psi_column(reference["features"][name], X[:, i]) for i, name in enumerate(feature_names)},
        "score": psi_column(reference["score"], scores),
    }
