"""API FastAPI: scoring sob demanda, consultas, stream SSE, drift, health — e o frontend
estático da demo, servido pela própria API (padrão do rag-knowledge-assistant).

Sobe com:  uvicorn --factory app.api.main:create_app
Todas as URLs consumidas pelo frontend são RELATIVAS, então funciona atrás de qualquer
prefixo removido pelo proxy (ex.: /projects/fraud/).
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.api.rate_limit import RateLimiter
from app.api.schemas import ScoreRequest, ScoreResponse
from app.api.services import Broadcaster, TTLCache, drift_report, score_transaction, sse_events
from app.config.settings import Settings, get_settings
from app.data.catalog import CATEGORY_NAMES
from app.data.geo import CITIES
from app.model.artifact import FraudModel
from app.storage.db import Database

log = logging.getLogger("api")
_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def make_broker_check(bootstrap_servers: str) -> Callable[[], bool]:
    from confluent_kafka.admin import AdminClient

    admin = AdminClient({"bootstrap.servers": bootstrap_servers, "socket.timeout.ms": 3000})

    def check() -> bool:
        try:
            admin.list_topics(timeout=2)
            return True
        except Exception:  # noqa: BLE001
            return False

    return check


def create_app(
    settings: Settings | None = None,
    model: FraudModel | None = None,
    db: Database | None = None,
    broker_check: Callable[[], bool] | None = None,
    static_dir: Path | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    owns_db = db is None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.model = model or FraudModel.load(settings.model_dir)
        if owns_db:
            from app.streaming.runtime import connect_db

            app.state.db = connect_db(settings)
        else:
            app.state.db = db
        app.state.broker_check = broker_check or make_broker_check(settings.kafka_bootstrap_servers)
        app.state.broadcaster = Broadcaster(app.state.db, settings.sse_poll_interval_seconds)
        await app.state.broadcaster.start()
        log.info("API pronta: modelo %s", app.state.model.version)
        yield
        await app.state.broadcaster.stop()
        if owns_db:
            app.state.db.close()

    app = FastAPI(title="Real-Time Fraud Detection", version="1.0.0", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    limiter = RateLimiter(settings.score_rate_limit_per_minute, settings.score_global_rate_limit_per_minute)
    stats_cache, drift_cache, health_cache = TTLCache(2.0), TTLCache(settings.drift_cache_seconds), TTLCache(5.0)

    @app.middleware("http")
    async def limit_body_size(request: Request, call_next):
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > settings.max_body_bytes:
            return JSONResponse({"detail": "Corpo da requisição grande demais."}, status_code=413)
        return await call_next(request)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        log.exception("erro inesperado em %s %s", request.method, request.url.path)  # detalhes só no log
        return JSONResponse({"detail": "Erro interno. Tente novamente em instantes."}, status_code=500)

    # ------------------------------------------------------------------ rotas
    @app.get("/health")
    def health():
        db_ok = app.state.db.ping()
        broker_ok = health_cache.get_or_compute("broker", app.state.broker_check)
        ok = db_ok and broker_ok
        body = {
            "status": "ok" if ok else "unhealthy",
            "model": {"loaded": True, "version": app.state.model.version},
            "database": {"ok": db_ok},
            "broker": {"ok": broker_ok},
        }
        return JSONResponse(body, status_code=200 if ok else 503)

    @app.get("/model")
    def model_info():
        m = app.state.model
        meta = m.meta
        return {
            "version": m.version,
            "model_type": m.model_type,
            "threshold": m.threshold,
            "threshold_criterion": meta.get("threshold_criterion"),
            "trained_at": meta.get("trained_at"),
            "data": meta.get("data"),
            "split_sizes": meta.get("split_sizes"),
            "features": m.feature_names,
            "metrics": meta.get("metrics"),
            "comparison": meta.get("comparison"),
            # Valores aceitos pelo /score: o formulário da demo os lê daqui (nunca fica defasado).
            "options": {
                "cities": [{"name": n, "country": c.country} for n, c in CITIES.items()],
                "categories": list(CATEGORY_NAMES),
            },
        }

    @app.get("/transactions")
    def transactions(limit: int = Query(50, ge=1, le=200), flagged_only: bool = False):
        return {"items": app.state.db.recent(limit, flagged_only)}

    @app.get("/stats")
    def stats(window_minutes: int = Query(15, ge=1, le=1440)):
        return stats_cache.get_or_compute(window_minutes, lambda: app.state.db.stats(window_minutes))

    @app.get("/drift")
    def drift(window_minutes: int | None = Query(None, ge=1, le=1440)):
        window_minutes = window_minutes or settings.drift_window_minutes
        return drift_cache.get_or_compute(
            window_minutes, lambda: drift_report(app.state.model, app.state.db, settings, window_minutes)
        )

    @app.post("/score", response_model=ScoreResponse, dependencies=[Depends(limiter)])
    def score(req: ScoreRequest):
        return score_transaction(app.state.model, app.state.db, req)

    @app.get("/stream")
    async def stream(request: Request):
        q = app.state.broadcaster.subscribe()
        if q is None:
            raise HTTPException(status_code=503, detail="Muitos clientes conectados. Tente de novo em instantes.")
        return StreamingResponse(
            sse_events(request.is_disconnected, app.state.broadcaster, q, settings.sse_heartbeat_seconds),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},  # o Nginx não deve bufferizar o stream
        )

    # Frontend estático por ÚLTIMO: as rotas acima têm precedência sobre o mount em "/".
    static = static_dir or _STATIC_DIR
    if static.exists():
        app.mount("/", StaticFiles(directory=static, html=True), name="static")
    return app
