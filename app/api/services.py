"""Serviços da API: scoring sob demanda (stateless), drift com cache e fan-out do stream."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

import numpy as np

from app.api.schemas import ScoreRequest, ScoreResponse
from app.config.settings import Settings
from app.data.geo import CITIES
from app.data.schema import Transaction
from app.features.engine import FEATURE_NAMES, UserState, compute_features, features_as_dict
from app.model.artifact import FraudModel
from app.model.drift import compute_drift
from app.storage.db import Database

log = logging.getLogger("api")

MIN_DRIFT_SAMPLES = 200
_FEATURE_INDEX = {n: i for i, n in enumerate(FEATURE_NAMES)}


def score_transaction(model: FraudModel, db: Database, req: ScoreRequest, top_n: int = 3) -> ScoreResponse:
    """Pontua sem persistir e sem alterar state (o visitante não polui métricas nem o pipeline).

    As features stateful vêm do histórico do `user_id` no banco (usuário desconhecido = cold
    start) e podem ser sobrescritas por `req.context` — usado pelos exemplos prontos da demo."""
    country = req.country or CITIES[req.city].country
    txn = Transaction(
        transaction_id=f"score-{uuid.uuid4().hex[:12]}", user_id=req.user_id, amount=req.amount, currency=req.currency,
        country=country, city=req.city, device_id=req.device_id, merchant_id=req.merchant_id,
        merchant_category=req.merchant_category, timestamp=req.timestamp or datetime.now(timezone.utc),
    )
    state = UserState.from_transactions(db.load_user_history(req.user_id, 500))
    vector = compute_features(state, txn)
    for key, value in (req.context or {}).items():
        vector[_FEATURE_INDEX[key]] = float(value)
    X = np.asarray([vector], dtype=np.float64)
    score = float(model.predict(X)[0])
    return ScoreResponse(
        score=score, flagged=model.is_flagged(score), threshold=model.threshold, model_version=model.version,
        top_features=model.explain(X, top_n), features=features_as_dict(vector),
    )


class TTLCache:
    """Cache minúsculo com TTL: protege o banco de quem martela /stats e /drift."""

    def __init__(self, ttl: float, clock: Callable[[], float] = time.monotonic):
        self.ttl, self._clock = ttl, clock
        self._items: dict[Any, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get_or_compute(self, key: Any, fn: Callable[[], Any]) -> Any:
        now = self._clock()
        with self._lock:
            hit = self._items.get(key)
            if hit and now - hit[0] < self.ttl:
                return hit[1]
        value = fn()
        with self._lock:
            self._items[key] = (now, value)
            if len(self._items) > 64:  # chaves = janelas pedidas; limita o crescimento
                self._items.pop(next(iter(self._items)))
        return value


def drift_report(model: FraudModel, db: Database, settings: Settings, window_minutes: int) -> dict:
    """PSI por feature e do score (janela recente vs referência do treino)."""
    rows, scores = db.window_features(window_minutes, model.version)
    base = {"window_minutes": window_minutes, "threshold": settings.drift_psi_threshold, "n": len(rows), "min_samples": MIN_DRIFT_SAMPLES}
    if len(rows) < MIN_DRIFT_SAMPLES:
        return {**base, "status": "insufficient_data", "features": {}, "score": None, "alerts": []}
    psi = compute_drift(model.meta["reference"], np.asarray(rows, dtype=np.float64), model.feature_names, np.asarray(scores))
    limit = settings.drift_psi_threshold
    alerts = sorted([n for n, v in psi["features"].items() if v > limit], key=lambda n: -psi["features"][n])
    if psi["score"] > limit:
        alerts.append("score")
    return {**base, "status": "alert" if alerts else "ok", "features": psi["features"], "score": psi["score"], "alerts": alerts}


class Broadcaster:
    """Um único poller do banco (custo constante) distribui as novas transações a todos os
    clientes SSE conectados, cada um com sua fila limitada (cliente lento perde as mais antigas)."""

    def __init__(self, db: Database, poll_interval: float = 1.0, max_clients: int = 100, queue_size: int = 200):
        self.db, self.poll_interval, self.max_clients, self.queue_size = db, poll_interval, max_clients, queue_size
        self._queues: set[asyncio.Queue] = set()
        self._task: asyncio.Task | None = None
        self.cursor = 0

    @property
    def clients(self) -> int:
        return len(self._queues)

    async def start(self) -> None:
        self.cursor = await asyncio.to_thread(self.db.max_seq)
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def subscribe(self) -> asyncio.Queue | None:
        if len(self._queues) >= self.max_clients:
            return None
        q: asyncio.Queue = asyncio.Queue(maxsize=self.queue_size)
        self._queues.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._queues.discard(q)

    async def _run(self) -> None:
        while True:
            try:
                rows = await asyncio.to_thread(self.db.since_seq, self.cursor, 200)
            except Exception:  # noqa: BLE001 — banco fora do ar não derruba o poller
                log.exception("broadcaster: falha ao consultar o banco")
                await asyncio.sleep(max(self.poll_interval, 2.0))
                continue
            for row in rows:
                self.cursor = row["seq"]
                if not self._queues:
                    continue
                for q in tuple(self._queues):
                    if q.full():
                        q.get_nowait()  # cliente lento: descarta o mais antigo
                    q.put_nowait(row)
            if len(rows) < 200:
                await asyncio.sleep(self.poll_interval)


async def sse_events(is_disconnected: Callable[[], Any], broadcaster: Broadcaster, q: asyncio.Queue, heartbeat: float):
    """Gerador de eventos SSE. `: ping` periódico mantém a conexão viva atrás de Nginx/Cloudflare."""
    try:
        yield "retry: 3000\n\nevent: ready\ndata: {}\n\n"
        while True:
            if await is_disconnected():
                break
            try:
                row = await asyncio.wait_for(q.get(), timeout=heartbeat)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            yield f"event: transaction\ndata: {json.dumps(row, ensure_ascii=False)}\n\n"
    finally:
        broadcaster.unsubscribe(q)
