"""Configuração de runtime via variáveis de ambiente (ou .env local).

Todos os valores têm um default seguro para desenvolvimento local, então o app
importa e os testes rodam sem nenhum .env real. Em produção os segredos
(DATABASE_URL com senha) vêm do .env da VPS, nunca do repositório.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Kafka
    kafka_bootstrap_servers: str = "127.0.0.1:29092"
    kafka_topic_transactions: str = "transactions"
    kafka_topic_rejected: str = "transactions.rejected"
    kafka_consumer_group: str = "fraud-scorer"

    # PostgreSQL
    database_url: str = "postgresql://fraud:fraud@localhost:5432/fraud"

    # Modelo (artefato versionado)
    model_dir: Path = PROJECT_ROOT / "models"

    # Retenção (banco): janela e teto de linhas, varridos periodicamente pelo scorer
    retention_hours: int = 48
    retention_max_rows: int = 200_000
    retention_sweep_seconds: int = 300

    # Produtor de replay (demo ao vivo)
    replay_rate_per_sec: float = 2.0
    replay_max_rate_per_sec: float = 20.0  # teto duro, independe do valor acima
    replay_users: int = 500
    replay_seed: int = 7
    replay_fraud_rate: float = 0.03
    replay_drift_feature: str = ""  # vazio = sem drift (padrão em produção). Suportado: "amount"
    replay_drift_factor: float = 1.0
    replay_warmup_days: int = 35  # histórico simulado publicado sem pausa no 1º boot (banco vazio); >= MEMORY_DAYS (30) p/ as features de janela longa estarem "quentes"
    replay_warmup_rate_per_sec: float = 250.0

    # API pública
    score_rate_limit_per_minute: int = 10  # por visitante
    score_global_rate_limit_per_minute: int = 120  # teto global (rede de segurança)
    max_body_bytes: int = 4096
    sse_poll_interval_seconds: float = 1.0
    sse_heartbeat_seconds: float = 15.0

    # Drift (PSI)
    drift_psi_threshold: float = 0.2
    # A janela precisa cobrir VÁRIOS ciclos diurnos simulados: com o relógio acelerado do replay
    # 1 dia simulado ≈ 19 min reais, e uma janela mais curta que isso enviesaria hour_of_day.
    drift_window_minutes: int = 60
    drift_cache_seconds: float = 15.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
