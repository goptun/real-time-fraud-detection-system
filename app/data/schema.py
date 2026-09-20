"""Schema único da transação: usado pelo gerador, pelo scorer (validação dos
eventos do Kafka) e pela API (corpo do POST /score)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.data.catalog import CATEGORY_NAMES
from app.data.geo import CITIES

FRAUD_PATTERNS = (
    "velocity",
    "amount_spike",
    "new_device",
    "impossible_travel",
    "low_and_slow",
)


class Transaction(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    transaction_id: str = Field(min_length=1, max_length=64)
    user_id: str = Field(min_length=1, max_length=64)
    amount: float = Field(gt=0, lt=10_000_000)
    currency: str = Field(default="BRL", min_length=3, max_length=3)
    country: str = Field(min_length=2, max_length=2)
    city: str = Field(min_length=1, max_length=64)
    device_id: str = Field(min_length=1, max_length=64)
    merchant_id: str = Field(min_length=1, max_length=64)
    merchant_category: str
    timestamp: datetime
    # Verdade-terreno: só existe em dados sintéticos rotulados. Ausente no /score.
    is_fraud: bool | None = None
    fraud_pattern: str | None = None

    @field_validator("merchant_category")
    @classmethod
    def _known_category(cls, v: str) -> str:
        if v not in CATEGORY_NAMES:
            raise ValueError(f"categoria desconhecida: {v}")
        return v

    @field_validator("timestamp")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        # Datetime sem timezone é interpretado como UTC (o sistema inteiro é UTC).
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)

    @field_validator("fraud_pattern")
    @classmethod
    def _known_pattern(cls, v: str | None) -> str | None:
        if v is not None and v not in FRAUD_PATTERNS:
            raise ValueError(f"padrão de fraude desconhecido: {v}")
        return v

    @model_validator(mode="after")
    def _location_is_known(self) -> "Transaction":
        city = CITIES.get(self.city)
        if city is None or city.country != self.country:
            raise ValueError(f"localização desconhecida: {self.country}/{self.city}")
        return self

    @property
    def epoch(self) -> float:
        return self.timestamp.timestamp()

    def to_event(self, *, with_label: bool = False) -> dict[str, Any]:
        """Payload JSON-serializável publicado no Kafka. O rótulo só vai junto
        quando pedido explicitamente (dataset/replay sintético)."""
        d = self.model_dump(mode="json", exclude={"is_fraud", "fraud_pattern"})
        if with_label:
            d["is_fraud"] = self.is_fraud
        return d
