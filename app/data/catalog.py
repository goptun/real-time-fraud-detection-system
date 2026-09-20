"""Categorias de merchant e o catálogo fixo de merchants sintéticos.

O catálogo é gerado com uma seed constante (independente da seed da simulação)
para que treino, replay ao vivo e a demo enxerguem sempre os mesmos merchants.
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np


class Category(NamedTuple):
    name: str
    legit_weight: float  # peso na escolha de merchants legítimos
    amount_shift: float  # deslocamento (log) do valor típico da categoria
    risky: bool  # categoria preferida por fraudadores


CATEGORIES: list[Category] = [
    Category("groceries", 22, 0.3, False),
    Category("restaurants", 20, 0.0, False),
    Category("fuel", 9, 0.2, False),
    Category("pharmacy", 8, -0.2, False),
    Category("transport", 9, -0.5, False),
    Category("entertainment", 7, -0.1, False),
    Category("utilities", 5, 0.4, False),
    Category("online_retail", 10, 0.2, True),
    Category("electronics", 3, 1.4, True),
    Category("travel", 2, 1.6, True),
    Category("jewelry", 0.5, 1.5, True),
    Category("gift_cards", 1, 0.4, True),
    Category("crypto_exchange", 0.5, 1.2, True),
]

CATEGORY_BY_NAME: dict[str, Category] = {c.name: c for c in CATEGORIES}
CATEGORY_NAMES: list[str] = [c.name for c in CATEGORIES]
RISKY_CATEGORIES: list[str] = [c.name for c in CATEGORIES if c.risky]
# Categorias onde uma compra grande e legítima é plausível (gera sobreposição de classes).
BIG_TICKET_CATEGORIES = ("electronics", "travel", "jewelry")

N_MERCHANTS = 300
_CATALOG_SEED = 1234


def build_merchants() -> list[tuple[str, str]]:
    """Lista fixa de (merchant_id, categoria)."""
    rng = np.random.default_rng(_CATALOG_SEED)
    weights = np.array([c.legit_weight for c in CATEGORIES], dtype=float)
    weights /= weights.sum()
    # Os primeiros merchants percorrem as categorias em rodízio (3 por categoria):
    # garante que até as raras (jewelry, crypto) tenham merchant. O resto segue os pesos.
    n_seeded = 3 * len(CATEGORIES)
    idx = [i % len(CATEGORIES) for i in range(n_seeded)]
    idx += rng.choice(len(CATEGORIES), size=N_MERCHANTS - n_seeded, p=weights).tolist()
    return [(f"m_{i:03d}", CATEGORIES[int(c)].name) for i, c in enumerate(idx)]


MERCHANTS: list[tuple[str, str]] = build_merchants()
MERCHANT_CATEGORY: dict[str, str] = dict(MERCHANTS)
