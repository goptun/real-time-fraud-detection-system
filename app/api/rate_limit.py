"""Rate limiting em memória, por visitante REAL, com teto global.

Por que não `request.client.host`: atrás do Nginx (e do Cloudflare) o peer TCP é sempre o
gateway do Docker, então todos os visitantes dividiriam um único bucket. O visitante real
vem de `CF-Connecting-IP` (Cloudflare) ou do primeiro `X-Forwarded-For` (Nginx).
Risco aceito: quem acessar a origem sem passar pela Cloudflare pode forjar o cabeçalho e
ganhar buckets novos — o teto GLOBAL limita o dano (é uma demo, não um controle de acesso).
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable

from fastapi import HTTPException, Request

WINDOW_SECONDS = 60.0
MAX_TRACKED_VISITORS = 10_000


def visitor_key(request: Request) -> str:
    h = request.headers
    if cf := h.get("cf-connecting-ip"):
        return cf.strip()[:64]
    if xff := h.get("x-forwarded-for"):
        return xff.split(",")[0].strip()[:64]
    return request.client.host if request.client else "unknown"


class RateLimiter:
    def __init__(self, per_visitor: int, global_limit: int, window: float = WINDOW_SECONDS, clock: Callable[[], float] = time.monotonic):
        self.per_visitor, self.global_limit, self.window, self._clock = per_visitor, global_limit, window, clock
        self._hits: dict[str, deque[float]] = {}
        self._global: deque[float] = deque()
        self._lock = threading.Lock()

    @staticmethod
    def _trim(q: deque[float], cutoff: float) -> None:
        while q and q[0] <= cutoff:
            q.popleft()

    def allow(self, key: str) -> tuple[bool, float]:
        """(permitido, segundos até poder tentar de novo)."""
        now = self._clock()
        cutoff = now - self.window
        with self._lock:
            self._trim(self._global, cutoff)
            q = self._hits.setdefault(key, deque())
            self._trim(q, cutoff)
            if len(self._global) >= self.global_limit:
                return False, max(self._global[0] + self.window - now, 1.0)
            if len(q) >= self.per_visitor:
                return False, max(q[0] + self.window - now, 1.0)
            q.append(now)
            self._global.append(now)
            if len(self._hits) > MAX_TRACKED_VISITORS:  # limita a memória: descarta quem não tem hits recentes
                for k in [k for k, v in self._hits.items() if not v or v[-1] <= cutoff]:
                    del self._hits[k]
            return True, 0.0

    def __call__(self, request: Request) -> None:
        """Dependency do FastAPI: HTTP 429 (com Retry-After) quando excede."""
        ok, retry = self.allow(visitor_key(request))
        if not ok:
            raise HTTPException(
                status_code=429,
                detail="Muitas requisições em pouco tempo. Aguarde um instante e tente de novo.",
                headers={"Retry-After": str(int(retry) + 1)},
            )
