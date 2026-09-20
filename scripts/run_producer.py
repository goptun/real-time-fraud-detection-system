"""Sobe o produtor de replay (transações sintéticas contínuas → Kafka)."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import get_settings  # noqa: E402
from app.streaming.replay import ReplayProducer  # noqa: E402
from app.streaming.runtime import connect_db, install_signal_handlers, setup_logging  # noqa: E402


def main() -> None:
    setup_logging()
    settings = get_settings()
    db = connect_db(settings)  # usado só p/ retomar o relógio simulado depois do último evento persistido
    stop = threading.Event()
    install_signal_handlers(stop)
    ReplayProducer(settings, db).run(stop)
    db.close()


if __name__ == "__main__":
    main()
