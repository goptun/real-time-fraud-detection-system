"""Produtor de replay: publica no Kafka um fluxo contínuo de transações sintéticas.

Relógio de simulação ACELERADO. O comportamento por usuário precisa bater com o do
treino (~4-5 transações/usuário/dia), mas uma demo viva precisa de alguns eventos por
segundo. Então o `Simulator` avança dia a dia no tempo SIMULADO e os eventos saem no
ritmo configurado em tempo REAL (ex.: 2/s com 500 usuários ≈ 1 dia simulado a cada
~19 min). Os timestamps dos eventos são simulados; latência, retenção e janelas do
dashboard usam o relógio real (`scored_at`, timestamp do Kafka).

No primeiro boot (banco vazio) publica antes `replay_warmup_days` de histórico sem
pausa, para os usuários não começarem todos "frios" (o que distorceria features e o
monitoramento de drift). Num reboot, o relógio simulado continua depois do último
evento persistido, nunca voltando no tempo.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterator

from confluent_kafka import KafkaException, Producer

from app.config.settings import Settings
from app.data.schema import Transaction
from app.data.simulator import SimConfig, Simulator
from app.storage.db import Database
from app.streaming.kafka_utils import make_producer, publish_transaction

log = logging.getLogger("replay")

SUPPORTED_DRIFT_FEATURES = ("amount",)


class Pacer:
    """Mantém um ritmo médio de `rate` eventos/s (relógio e sleep injetáveis p/ teste)."""

    def __init__(self, rate: float, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        if rate <= 0:
            raise ValueError("rate deve ser positivo")
        self.interval, self._clock, self._sleep = 1.0 / rate, clock, sleep
        self._next = clock()

    def wait(self) -> None:
        now = self._clock()
        if self._next > now:
            self._sleep(self._next - now)
        else:
            self._next = now  # ficou para trás: não tenta "compensar" com uma rajada
        self._next += self.interval


def effective_rate(settings: Settings) -> float:
    """Ritmo configurado, limitado pelo teto duro (protege a VPS de configuração errada)."""
    return max(0.01, min(settings.replay_rate_per_sec, settings.replay_max_rate_per_sec))


def apply_drift(txn: Transaction, feature: str, factor: float) -> Transaction:
    """Desloca a distribuição de uma feature para validar o monitoramento de drift."""
    if not feature or factor == 1.0:
        return txn
    if feature not in SUPPORTED_DRIFT_FEATURES:
        raise ValueError(f"drift não suportado para '{feature}' (suportados: {SUPPORTED_DRIFT_FEATURES})")
    return txn.model_copy(update={"amount": round(txn.amount * factor, 2)})


def resolve_start(db: Database | None, now: datetime | None = None) -> tuple[datetime, bool]:
    """(início do relógio simulado, precisa_de_warmup). Nunca volta antes do último evento persistido."""
    now = now or datetime.now(timezone.utc)
    last = db.max_event_time() if db is not None else None
    if last is None:
        return now, True
    return max(now, last + timedelta(seconds=1)), False


class ReplayProducer:
    def __init__(self, settings: Settings, db: Database | None = None, producer: Producer | None = None, run_id: str | None = None):
        self.settings, self.db = settings, db
        self.producer = producer or make_producer(settings.kafka_bootstrap_servers)
        self.run_id = run_id or format(int(time.time()), "x")
        self.published = 0

    def _publish(self, txn: Transaction) -> None:
        txn = apply_drift(txn, self.settings.replay_drift_feature, self.settings.replay_drift_factor)
        while True:
            try:
                publish_transaction(self.producer, self.settings.kafka_topic_transactions, txn, with_label=True)
                break
            except BufferError:  # fila local cheia: deixa o produtor escoar
                self.producer.poll(0.5)
        self.producer.poll(0)
        self.published += 1

    def _simulator(self, start: datetime) -> Simulator:
        s = self.settings
        # id_prefix por execução: reiniciar com a mesma seed não pode reemitir ids já
        # persistidos (o INSERT idempotente os descartaria em silêncio).
        return Simulator(SimConfig(n_users=s.replay_users, seed=s.replay_seed, fraud_rate=s.replay_fraud_rate, start=start, id_prefix=f"r{self.run_id}"))

    def events(self, start: datetime, warmup: bool) -> Iterator[tuple[Transaction, bool]]:
        """Gera (transação, é_warmup). Warm-up primeiro, depois o fluxo contínuo (infinito)."""
        sim_start = start - timedelta(days=self.settings.replay_warmup_days) if warmup else start
        sim = self._simulator(sim_start)
        if warmup:
            for txn in sim.advance(self.settings.replay_warmup_days):
                yield txn, True
        while True:
            for txn in sim.advance(1):
                yield txn, False

    def run(self, stop: threading.Event) -> None:
        start, warmup = resolve_start(self.db)
        rate = effective_rate(self.settings)
        log.info("replay: início simulado=%s warmup=%s ritmo=%.2f/s usuários=%d drift=%s×%.2f",
                 start.isoformat(timespec="seconds"), warmup, rate, self.settings.replay_users,
                 self.settings.replay_drift_feature or "-", self.settings.replay_drift_factor)
        live_pacer = Pacer(rate)
        warm_pacer = Pacer(self.settings.replay_warmup_rate_per_sec)
        was_warmup, last_report = warmup, time.monotonic()
        try:
            for txn, is_warm in self.events(start, warmup):
                if stop.is_set():
                    break
                if was_warmup and not is_warm:
                    log.info("warmup concluído (%d eventos); seguindo em tempo real", self.published)
                    was_warmup = False
                    live_pacer = Pacer(rate)
                (warm_pacer if is_warm else live_pacer).wait()
                self._publish(txn)
                if time.monotonic() - last_report >= 30:
                    log.info("replay: %d eventos publicados", self.published)
                    last_report = time.monotonic()
        except KafkaException:
            log.exception("falha no produtor Kafka")
            raise
        finally:
            self.producer.flush(10)
            log.info("replay encerrado (%d eventos)", self.published)
