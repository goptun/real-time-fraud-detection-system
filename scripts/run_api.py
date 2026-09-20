"""Sobe a API (e o frontend da demo). Local: http://127.0.0.1:8002/"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8002)
    ap.add_argument("--reload", action="store_true")
    a = ap.parse_args()
    uvicorn.run("app.api.main:create_app", factory=True, host=a.host, port=a.port, reload=a.reload, log_level="info")


if __name__ == "__main__":
    main()
