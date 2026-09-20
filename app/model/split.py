"""Split temporal: treino no passado, validação no meio, teste no futuro."""

from __future__ import annotations

import numpy as np

from app.features.dataset import FeatureDataset


def temporal_split(
    ds: FeatureDataset, fractions: tuple[float, float, float] = (0.6, 0.2, 0.2)
) -> tuple[FeatureDataset, FeatureDataset, FeatureDataset]:
    """Corta por *timestamp* (quantis de tempo), não por sorteio: todo evento de
    treino é estritamente anterior a todo evento de validação, que é estritamente
    anterior ao de teste. Empates de timestamp ficam do mesmo lado do corte."""
    if abs(sum(fractions) - 1.0) > 1e-9 or min(fractions) <= 0:
        raise ValueError("fractions deve somar 1 e ser positivo")
    if len(ds) < 3 or np.any(np.diff(ds.ts) < 0):
        raise ValueError("dataset precisa estar ordenado por tempo")
    c1, c2 = np.quantile(ds.ts, [fractions[0], fractions[0] + fractions[1]])
    return (
        ds.subset(ds.ts < c1),
        ds.subset((ds.ts >= c1) & (ds.ts < c2)),
        ds.subset(ds.ts >= c2),
    )
