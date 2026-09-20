"""Métricas para classe rara e escolha explícita do threshold de decisão.

Acurácia NÃO é usada: com 2% de fraude, "nunca sinalizar" já acerta 98%.
"""

from __future__ import annotations

import numpy as np
from sklearn.metrics import average_precision_score, precision_recall_curve, roc_curve


def pr_auc(y: np.ndarray, score: np.ndarray) -> float:
    return float(average_precision_score(y, score))


def metrics_at_threshold(y: np.ndarray, score: np.ndarray, threshold: float) -> dict:
    """Regra de decisão: sinaliza quando score >= threshold."""
    y = np.asarray(y).astype(bool)
    flagged = np.asarray(score) >= threshold
    tp = int(np.sum(flagged & y))
    fp = int(np.sum(flagged & ~y))
    fn = int(np.sum(~flagged & y))
    tn = int(np.sum(~flagged & ~y))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "threshold": float(threshold),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "fpr": fp / (fp + tn) if fp + tn else 0.0,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "flagged_rate": float(flagged.mean()),
    }


def select_threshold(y_val: np.ndarray, score_val: np.ndarray, max_fpr: float = 0.01) -> float:
    """Critério documentado: MAIOR recall sujeito a FPR <= max_fpr, medido só em
    dados de VALIDAÇÃO. (O conjunto de teste nunca entra aqui.) Sinaliza score >= t."""
    y_val = np.asarray(y_val).astype(int)
    if y_val.sum() == 0 or y_val.sum() == len(y_val):
        raise ValueError("validação precisa ter as duas classes para escolher o threshold")
    fpr, tpr, thr = roc_curve(y_val, score_val, drop_intermediate=False)
    feasible = np.flatnonzero(fpr <= max_fpr)
    t = float(thr[feasible[-1]])  # tpr é não-decrescente: o último viável tem o maior recall
    if not np.isfinite(t):  # nenhum ponto viável sinaliza algo
        t = float(np.nextafter(np.max(score_val), np.inf))
    return t


def evaluate_candidate(
    y_val: np.ndarray, score_val: np.ndarray, y_test: np.ndarray, score_test: np.ndarray, max_fpr: float
) -> dict:
    """Threshold escolhido na validação; teste só reporta. Por construção o
    threshold não recebe nenhum dado de teste."""
    thr = select_threshold(y_val, score_val, max_fpr)
    return {
        "threshold": thr,
        "val_pr_auc": pr_auc(y_val, score_val),
        "test_pr_auc": pr_auc(y_test, score_test),
        "val": metrics_at_threshold(y_val, score_val, thr),
        "test": metrics_at_threshold(y_test, score_test, thr),
        "test_fraud_rate": float(np.mean(y_test)),
    }


def precision_recall_points(y: np.ndarray, score: np.ndarray):
    p, r, _ = precision_recall_curve(y, score)
    return p, r
