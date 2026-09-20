"""Serviço de scoring em streaming.

Loop por mensagem:
  parse+validação → (inválida? dead-letter) → state do usuário (LRU; miss = reconstrói do
  PostgreSQL) → features → score → INSERT idempotente → se inseriu, atualiza o state →
  (no fim do lote) commit do offset.

Garantias:
  * at-least-once: o offset só é commitado depois da escrita no banco;
  * reentrega = no máximo um registro (PK transaction_id) e o state não conta duas vezes;
  * uma mensagem inválida vai ao tópico dead-letter e o consumo continua;
  * falha transitória do banco: tenta de novo; persistindo, o processo cai e o
    orquestrador o reinicia (crash-only) — retoma do último offset commitado.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass

import numpy as np
import psycopg
from confluent_kafka import Consumer, KafkaException, Producer
from psycopg_pool import PoolTimeout
from pydantic import ValidationError

from app.config.settings import Settings
from app.data.schema import Transaction
from app.features.engine import FEATURE_NAMES, UserStateStore, compute_features, update_state
from app.model.artifact import FraudModel
from app.storage.db import Database, ScoredTransaction

log = logging.getLogger("scorer")

SCORED, DUPLICATE, REJECTED = "scored", "duplicate", "rejected"
DB_RETRIES = 5
TRANSIENT_DB_ERRORS = (psycopg.OperationalError, psycopg.InterfaceError, PoolTimeout)


@dataclass
class Counters:
    scored: int = 0
    duplicates: int = 0
    rejected: int = 0


class ScoringService:
    def __init__(
        self,
        model: FraudModel,
        db: Database,
        settings: Settings,
        *,
        topic: str | None = None,
        dlq_topic: str | None = None,
        group_id: str | None = None,
        state_capacity: int = 20_000,
        history_limit: int = 500,
    ):
        if list(FEATURE_NAMES) != model.feature_names:
            raise RuntimeError("as features do artefato não batem com a máquina de estados (retreine o modelo)")
        self.model, self.db, self.settings = model, db, settings
        self.topic = topic or settings.kafka_topic_transactions
        self.dlq_topic = dlq_topic or settings.kafka_topic_rejected
        self.group_id = group_id or settings.kafka_consumer_group
        self.store = UserStateStore(state_capacity, loader=lambda uid: db.load_user_history(uid, history_limit))
        self.counters = Counters()

    # ---------------------------------------------------------- uma mensagem
    def process(self, value: bytes | str | None, kafka_ts_ms: float | None = None) -> tuple[str, str | None]:
        """Processa uma mensagem. Retorna (SCORED|DUPLICATE|REJECTED, motivo_da_rejeição)."""
        try:
            payload = json.loads(value if value is not None else b"")
            if not isinstance(payload, dict):
                return REJECTED, "payload não é um objeto JSON"
            txn = Transaction.model_validate(payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return REJECTED, "JSON inválido"
        except ValidationError as exc:
            first = exc.errors()[0]
            return REJECTED, f"schema inválido: {'.'.join(map(str, first['loc']))}: {first['msg']}"

        state = self.store.get(txn.user_id)
        vector = compute_features(state, txn)
        score = float(self.model.predict(np.asarray([vector], dtype=np.float64))[0])
        latency = max(0.0, time.time() * 1000.0 - kafka_ts_ms) if kafka_ts_ms else None
        rec = ScoredTransaction(txn, vector, score, self.model.is_flagged(score), self.model.threshold, self.model.version, latency)

        if not self._insert_with_retry(rec):
            return DUPLICATE, None
        update_state(state, txn)  # só depois de persistir
        return SCORED, None

    def _insert_with_retry(self, rec: ScoredTransaction) -> bool:
        for attempt in range(1, DB_RETRIES + 1):
            try:
                return self.db.insert_scored(rec)
            except TRANSIENT_DB_ERRORS as exc:
                if attempt == DB_RETRIES:
                    raise  # crash-only: o orquestrador reinicia e retoma do último offset commitado
                log.warning("banco indisponível (tentativa %d/%d): %s", attempt, DB_RETRIES, exc)
                time.sleep(min(2.0 * attempt, 8.0))
        raise RuntimeError("inalcançável")

    # ------------------------------------------------------------ loop Kafka
    def _consumer(self) -> Consumer:
        c = Consumer(
            {
                "bootstrap.servers": self.settings.kafka_bootstrap_servers,
                "group.id": self.group_id,
                "auto.offset.reset": "earliest",
                "enable.auto.commit": False,  # commit manual, depois de persistir
                "session.timeout.ms": 15000,
            }
        )
        c.subscribe([self.topic])
        return c

    def run(self, stop: threading.Event) -> None:
        consumer = self._consumer()
        producer = Producer({"bootstrap.servers": self.settings.kafka_bootstrap_servers})
        last_sweep = last_report = time.monotonic()
        log.info("scorer iniciado: topic=%s group=%s modelo=%s", self.topic, self.group_id, self.model.version)
        try:
            while not stop.is_set():
                # poll devolve assim que há UMA mensagem; depois drena o que já está no buffer sem
                # bloquear. (consume(timeout=1.0) esperaria encher o lote: ~1 s de latência com tráfego baixo.)
                first = consumer.poll(1.0)
                batch = [first] if first is not None else []
                if first is not None:
                    batch += consumer.consume(num_messages=99, timeout=0.0)
                for msg in batch:
                    if msg.error():
                        log.error("erro do consumidor: %s", msg.error())
                        continue
                    ts_type, ts_ms = msg.timestamp()
                    outcome, reason = self.process(msg.value(), ts_ms if ts_type == 1 else None)  # 1 = CreateTime
                    if outcome == SCORED:
                        self.counters.scored += 1
                    elif outcome == DUPLICATE:
                        self.counters.duplicates += 1
                    else:
                        self.counters.rejected += 1
                        self._dead_letter(producer, msg, reason)
                if batch:
                    producer.flush(5)
                    self._commit(consumer)
                now = time.monotonic()
                if now - last_sweep >= self.settings.retention_sweep_seconds:
                    removed = self.db.sweep_retention(self.settings.retention_hours, self.settings.retention_max_rows)
                    log.info("retenção: %d linhas removidas", removed)
                    last_sweep = now
                if now - last_report >= 30:
                    c = self.counters
                    log.info("scorer: scored=%d duplicates=%d rejected=%d state_cache=%d", c.scored, c.duplicates, c.rejected, len(self.store))
                    last_report = now
        finally:
            consumer.close()
            log.info("scorer encerrado")

    @staticmethod
    def _commit(consumer: Consumer) -> None:
        try:
            consumer.commit(asynchronous=False)
        except KafkaException as exc:
            if exc.args[0].code() != -168:  # _NO_OFFSET: nada novo a commitar
                raise

    def _dead_letter(self, producer: Producer, msg, reason: str | None) -> None:
        raw = msg.value() or b""
        body = {
            "reason": reason,
            "raw": raw[:2000].decode("utf-8", errors="replace"),
            "source": {"topic": msg.topic(), "partition": msg.partition(), "offset": msg.offset()},
        }
        producer.produce(self.dlq_topic, json.dumps(body, ensure_ascii=False).encode())
        log.warning("mensagem rejeitada (%s:%s@%s): %s", msg.topic(), msg.partition(), msg.offset(), reason)
