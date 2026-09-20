"""Boilerplate dos processos de longa duração (scorer, produtor de replay)."""

from __future__ import annotations

import logging
import signal
import threading
import time

from app.config.settings import Settings
from app.storage.db import Database

log = logging.getLogger("runtime")


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def install_signal_handlers(stop: threading.Event) -> None:
    """SIGTERM/SIGINT → encerramento gracioso (o `docker stop` envia SIGTERM)."""
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())


def connect_db(settings: Settings, timeout_s: float = 90.0) -> Database:
    """Conecta e cria o schema (idempotente). O banco pode ainda estar subindo: tenta por até `timeout_s`."""
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            db = Database(settings.database_url)
            db.init_schema()
            return db
        except Exception as exc:  # noqa: BLE001
            if time.monotonic() > deadline:
                raise
            log.warning("aguardando o banco: %s", exc)
            time.sleep(2)
