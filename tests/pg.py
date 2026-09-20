"""Helper de testes contra PostgreSQL real (schema isolado por classe de teste).
Os testes que dependem dele são pulados se o banco não estiver acessível."""

from __future__ import annotations

import os
import uuid

import psycopg

from app.storage.db import Database

DSN = os.environ.get("TEST_DATABASE_URL", "postgresql://fraud:fraud@localhost:5432/fraud")


def pg_available() -> bool:
    try:
        with psycopg.connect(DSN, connect_timeout=2):
            return True
    except Exception:
        return False


def make_db() -> Database:
    db = Database(DSN, schema=f"test_{uuid.uuid4().hex[:10]}")
    db.init_schema()
    return db
