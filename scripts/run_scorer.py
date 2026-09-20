"""Sobe o serviço de scoring (consumidor Kafka → features → modelo → PostgreSQL)."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import get_settings  # noqa: E402
from app.model.artifact import FraudModel  # noqa: E402
from app.streaming.runtime import connect_db, install_signal_handlers, setup_logging  # noqa: E402
from app.streaming.scorer import ScoringService  # noqa: E402


def main() -> None:
    setup_logging()
    settings = get_settings()
    model = FraudModel.load(settings.model_dir)
    db = connect_db(settings)
    stop = threading.Event()
    install_signal_handlers(stop)
    ScoringService(model, db, settings).run(stop)
    db.close()


if __name__ == "__main__":
    main()
