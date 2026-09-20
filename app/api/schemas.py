"""Contratos HTTP da API."""

from __future__ import annotations

import math
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.data.catalog import CATEGORY_NAMES
from app.data.geo import CITIES

# Features "stateful" que o `context` pode sobrescrever (simular um histórico sem ter um usuário real).
CONTEXT_FLAGS = ("is_new_device", "is_new_city", "is_new_country", "is_new_merchant")
CONTEXT_NUMERIC = (
    "txn_count_1m", "txn_count_10m", "txn_count_1h", "txn_count_24h", "txn_count_30d",
    "seconds_since_last", "km_from_last", "implied_speed_kmh", "amount_zscore_user", "amount_ratio_to_user_max",
)
CONTEXT_KEYS = CONTEXT_FLAGS + CONTEXT_NUMERIC


class ScoreRequest(BaseModel):
    """Transação a pontuar. Sem rótulo (`is_fraud`) — campos desconhecidos são rejeitados."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    user_id: str = Field(default="demo-user", min_length=1, max_length=64)
    amount: float = Field(gt=0, lt=10_000_000)
    currency: str = Field(default="BRL", min_length=3, max_length=3)
    city: str = Field(min_length=1, max_length=64)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    device_id: str = Field(default="demo-device", min_length=1, max_length=64)
    merchant_id: str = Field(default="m_custom", min_length=1, max_length=64)
    merchant_category: str
    timestamp: datetime | None = None
    context: dict[str, float] | None = None

    @field_validator("city")
    @classmethod
    def _known_city(cls, v: str) -> str:
        if v not in CITIES:
            raise ValueError(f"cidade desconhecida: {v}")
        return v

    @field_validator("merchant_category")
    @classmethod
    def _known_category(cls, v: str) -> str:
        if v not in CATEGORY_NAMES:
            raise ValueError(f"categoria desconhecida: {v}")
        return v

    @field_validator("context")
    @classmethod
    def _valid_context(cls, v: dict[str, float] | None) -> dict[str, float] | None:
        if v is None:
            return v
        for key, val in v.items():
            if key not in CONTEXT_KEYS:
                raise ValueError(f"chave de contexto inválida: {key} (permitidas: {', '.join(CONTEXT_KEYS)})")
            if not math.isfinite(val) or abs(val) > 1e7:
                raise ValueError(f"valor inválido para {key}")
            if key in CONTEXT_FLAGS and val not in (0, 1):
                raise ValueError(f"{key} deve ser 0 ou 1")
            if key in CONTEXT_NUMERIC and key not in ("amount_zscore_user",) and val < 0:
                raise ValueError(f"{key} não pode ser negativo")
        return v


class FeatureContribution(BaseModel):
    feature: str
    value: float
    contribution: float  # log-odds: > 0 empurra para "fraude"


class ScoreResponse(BaseModel):
    score: float
    flagged: bool
    threshold: float
    model_version: str
    top_features: list[FeatureContribution]
    features: dict[str, float]
