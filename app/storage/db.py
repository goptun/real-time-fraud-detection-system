"""Acesso ao PostgreSQL: schema, escrita idempotente, histórico por usuário e retenção.

Tempo: `event_time` é o timestamp do evento (SIMULADO no replay, que roda com
relógio acelerado); `scored_at` é o relógio real de quando o score foi gravado.
Retenção, janelas de estatística e drift usam `scored_at`; features usam `event_time`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg_pool import ConnectionPool

from app.data.schema import Transaction

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS transactions (
    seq               BIGSERIAL UNIQUE NOT NULL,
    transaction_id    TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL,
    amount            DOUBLE PRECISION NOT NULL,
    currency          TEXT NOT NULL,
    country           TEXT NOT NULL,
    city              TEXT NOT NULL,
    device_id         TEXT NOT NULL,
    merchant_id       TEXT NOT NULL,
    merchant_category TEXT NOT NULL,
    event_time        TIMESTAMPTZ NOT NULL,
    features          DOUBLE PRECISION[] NOT NULL,
    score             DOUBLE PRECISION NOT NULL,
    flagged           BOOLEAN NOT NULL,
    threshold         DOUBLE PRECISION NOT NULL,
    model_version     TEXT NOT NULL,
    is_fraud          BOOLEAN,
    scored_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    latency_ms        DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS transactions_user_time_idx ON transactions (user_id, event_time);
CREATE INDEX IF NOT EXISTS transactions_scored_at_idx ON transactions (scored_at);
CREATE INDEX IF NOT EXISTS transactions_flagged_seq_idx ON transactions (seq) WHERE flagged;
"""

_HISTORY_COLS = "transaction_id, user_id, amount, currency, country, city, device_id, merchant_id, merchant_category, event_time, is_fraud"


@dataclass
class ScoredTransaction:
    txn: Transaction
    features: list[float]
    score: float
    flagged: bool
    threshold: float
    model_version: str
    latency_ms: float | None = None


# ---------------------------------------------------------------------------
# Consultas de leitura (API). Janelas usam `scored_at` (relógio real).
# ---------------------------------------------------------------------------
_ROW_COLS = (
    "seq, transaction_id, user_id, amount, currency, country, city, merchant_id, merchant_category, "
    "event_time, score, flagged, threshold, model_version, is_fraud, scored_at, latency_ms"
)


def _row_to_dict(r: tuple) -> dict:
    return {
        "seq": r[0], "transaction_id": r[1], "user_id": r[2], "amount": r[3], "currency": r[4],
        "country": r[5], "city": r[6], "merchant_id": r[7], "merchant_category": r[8],
        "event_time": r[9].astimezone(timezone.utc).isoformat(), "score": r[10], "flagged": r[11],
        "threshold": r[12], "model_version": r[13], "is_fraud": r[14],
        "scored_at": r[15].astimezone(timezone.utc).isoformat(),
        "latency_ms": r[16],
    }


class _Reads:
    """Mixin com as consultas de leitura (mantém a classe Database enxuta)."""

    def recent(self, limit: int = 50, flagged_only: bool = False) -> list[dict]:
        where = "WHERE flagged" if flagged_only else ""
        with self.pool.connection() as conn:
            rows = conn.execute(f"SELECT {_ROW_COLS} FROM transactions {where} ORDER BY seq DESC LIMIT %s", (limit,)).fetchall()
        return [_row_to_dict(r) for r in rows]

    def since_seq(self, seq: int, limit: int = 200) -> list[dict]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                f"SELECT {_ROW_COLS} FROM transactions WHERE seq > %s ORDER BY seq ASC LIMIT %s", (seq, limit)
            ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def max_seq(self) -> int:
        with self.pool.connection() as conn:
            return conn.execute("SELECT coalesce(max(seq), 0) FROM transactions").fetchone()[0]

    def stats(self, window_minutes: int) -> dict:
        w = (window_minutes,)
        with self.pool.connection() as conn:
            (total, flagged, last_minute, p50, p95, tp, fp, fn, tn) = conn.execute(
                """
                SELECT count(*),
                       count(*) FILTER (WHERE flagged),
                       count(*) FILTER (WHERE scored_at > now() - interval '1 minute'),
                       percentile_cont(0.5)  WITHIN GROUP (ORDER BY latency_ms),
                       percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms),
                       count(*) FILTER (WHERE is_fraud IS TRUE  AND flagged),
                       count(*) FILTER (WHERE is_fraud IS FALSE AND flagged),
                       count(*) FILTER (WHERE is_fraud IS TRUE  AND NOT flagged),
                       count(*) FILTER (WHERE is_fraud IS FALSE AND NOT flagged)
                FROM transactions WHERE scored_at > now() - make_interval(mins => %s)
                """,
                w,
            ).fetchone()
            hist_rows = conn.execute(
                """
                SELECT least(width_bucket(score, 0, 1, 10), 10) AS b, count(*)
                FROM transactions WHERE scored_at > now() - make_interval(mins => %s) GROUP BY 1
                """,
                w,
            ).fetchall()
            timeline = conn.execute(
                """
                SELECT date_trunc('minute', scored_at) AS t, count(*), count(*) FILTER (WHERE flagged)
                FROM transactions WHERE scored_at > now() - make_interval(mins => %s) GROUP BY 1 ORDER BY 1
                """,
                w,
            ).fetchall()
        hist = [0] * 10
        for b, c in hist_rows:
            hist[max(1, int(b)) - 1] += c
        labeled = tp + fp + fn + tn
        return {
            "window_minutes": window_minutes,
            "total": total,
            "flagged": flagged,
            "flag_rate": flagged / total if total else 0.0,
            "last_minute": last_minute,
            "latency_ms": {"p50": p50, "p95": p95},
            "score_histogram": hist,
            "timeline": [{"t": t.astimezone(timezone.utc).isoformat(), "count": c, "flagged": f} for t, c, f in timeline],
            # Só o replay sintético tem verdade-terreno: qualidade "ao vivo" do modelo sobre o feed.
            "labeled": (
                {
                    "n": labeled, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
                    "precision": tp / (tp + fp) if tp + fp else None,
                    "recall": tp / (tp + fn) if tp + fn else None,
                }
                if labeled
                else None
            ),
        }

    def window_features(self, window_minutes: int, model_version: str, limit: int = 5000) -> tuple[list[list[float]], list[float]]:
        """(features, scores) das transações mais recentes da janela, só do modelo em vigor
        (a ordem das features é a do artefato que as gerou)."""
        with self.pool.connection() as conn:
            rows = conn.execute(
                """
                SELECT features, score FROM transactions
                WHERE scored_at > now() - make_interval(mins => %s) AND model_version = %s
                ORDER BY seq DESC LIMIT %s
                """,
                (window_minutes, model_version, limit),
            ).fetchall()
        return [r[0] for r in rows], [r[1] for r in rows]


class Database(_Reads):
    def __init__(self, dsn: str, schema: str | None = None, min_size: int = 1, max_size: int = 5):
        self.schema = schema
        kwargs: dict[str, Any] = {"autocommit": True}
        if schema:
            with psycopg.connect(dsn, autocommit=True) as c:
                c.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            kwargs["options"] = f"-c search_path={schema}"
        self.pool = ConnectionPool(dsn, min_size=min_size, max_size=max_size, kwargs=kwargs, open=True)
        self._dsn = dsn

    def close(self) -> None:
        self.pool.close()

    def drop_schema(self) -> None:
        """Só para testes: remove o schema isolado criado no __init__."""
        if self.schema:
            self.pool.close()
            with psycopg.connect(self._dsn, autocommit=True) as c:
                c.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')

    def init_schema(self) -> None:
        with self.pool.connection() as conn:
            conn.execute(SCHEMA_SQL)

    def ping(self) -> bool:
        try:
            with self.pool.connection(timeout=3) as conn:
                conn.execute("SELECT 1")
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------ escrita
    def insert_scored(self, rec: ScoredTransaction) -> bool:
        """Insert idempotente por transaction_id. True se inseriu; False se já existia
        (reentrega) — nesse caso nada muda."""
        t = rec.txn
        with self.pool.connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO transactions
                    (transaction_id, user_id, amount, currency, country, city, device_id, merchant_id,
                     merchant_category, event_time, features, score, flagged, threshold, model_version,
                     is_fraud, latency_ms)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (transaction_id) DO NOTHING
                RETURNING seq
                """,
                (
                    t.transaction_id, t.user_id, t.amount, t.currency, t.country, t.city, t.device_id,
                    t.merchant_id, t.merchant_category, t.timestamp, rec.features, rec.score, rec.flagged,
                    rec.threshold, rec.model_version, t.is_fraud, rec.latency_ms,
                ),
            )
            return cur.fetchone() is not None

    def sweep_retention(self, hours: int, max_rows: int) -> int:
        """Remove linhas mais antigas que `hours` (relógio real) e o excedente acima de `max_rows`."""
        deleted = 0
        with self.pool.connection() as conn:
            deleted += conn.execute(
                "DELETE FROM transactions WHERE scored_at < now() - make_interval(hours => %s)", (hours,)
            ).rowcount
            deleted += conn.execute(
                """
                DELETE FROM transactions
                WHERE seq <= (SELECT seq FROM transactions ORDER BY seq DESC OFFSET %s LIMIT 1)
                """,
                (max_rows,),
            ).rowcount
        return deleted

    # ------------------------------------------------------------------ leitura
    def load_user_history(self, user_id: str, limit: int = 500) -> list[Transaction]:
        """Últimas `limit` transações do usuário (mais antigas primeiro): fonte de
        verdade para reconstruir o state em cache miss."""
        with self.pool.connection() as conn:
            rows = conn.execute(
                f"SELECT {_HISTORY_COLS} FROM transactions WHERE user_id = %s ORDER BY event_time DESC LIMIT %s",
                (user_id, limit),
            ).fetchall()
        return [
            Transaction.model_construct(
                transaction_id=r[0], user_id=r[1], amount=r[2], currency=r[3], country=r[4], city=r[5],
                device_id=r[6], merchant_id=r[7], merchant_category=r[8],
                timestamp=r[9].astimezone(timezone.utc), is_fraud=r[10], fraud_pattern=None,
            )
            for r in reversed(rows)
        ]

    def count(self) -> int:
        with self.pool.connection() as conn:
            return conn.execute("SELECT count(*) FROM transactions").fetchone()[0]

    def max_event_time(self) -> datetime | None:
        with self.pool.connection() as conn:
            return conn.execute("SELECT max(event_time) FROM transactions").fetchone()[0]
