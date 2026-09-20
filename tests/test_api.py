import asyncio
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.api.rate_limit import RateLimiter
from app.api.services import Broadcaster, sse_events
from app.config.settings import Settings, get_settings
from app.data.schema import Transaction
from app.data.simulator import SimConfig, Simulator
from app.features.dataset import build_dataset
from app.features.engine import FEATURE_NAMES
from app.model.artifact import FraudModel
from app.storage.db import ScoredTransaction
from tests.pg import make_db, pg_available

MODEL = FraudModel.load(get_settings().model_dir)
T0 = datetime(2026, 3, 1, 12, tzinfo=timezone.utc)
VALID = {"amount": 59.9, "city": "São Paulo", "merchant_category": "groceries"}
SUSPICIOUS = {
    "amount": 4200.0, "city": "Tokyo", "merchant_category": "crypto_exchange", "device_id": "dev_unknown",
    "context": {"is_new_device": 1, "is_new_city": 1, "is_new_country": 1, "is_new_merchant": 1, "txn_count_1m": 5,
                "txn_count_10m": 7, "txn_count_1h": 7, "seconds_since_last": 20, "km_from_last": 18500,
                "implied_speed_kmh": 5000, "amount_zscore_user": 4.5, "amount_ratio_to_user_max": 9},
}


def settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


def txn(i, user="u1", dt=0.0, **over):
    p = dict(transaction_id=f"t{i}", user_id=user, amount=50.0, country="BR", city="São Paulo", device_id="d1",
             merchant_id="m_000", merchant_category="groceries", timestamp=T0 + timedelta(seconds=dt))
    p.update(over)
    return Transaction(**p)


def scored(i, score=0.1, flagged=False, fraud=None, latency=20.0, **kw):
    rec = ScoredTransaction(txn(i, **kw), [0.0] * len(FEATURE_NAMES), score, flagged, MODEL.threshold, MODEL.version, latency)
    rec.txn = rec.txn.model_copy(update={"is_fraud": fraud})
    return rec


class ApiCase(unittest.TestCase):
    broker_ok = True
    static_dir = None

    def make_client(self, **settings_kw):
        self.db = make_db()
        self.addCleanup(self.db.drop_schema)
        self.broker = {"ok": True}
        app = create_app(settings(sse_poll_interval_seconds=0.1, **settings_kw), MODEL, self.db,
                         broker_check=lambda: self.broker["ok"], static_dir=self.static_dir)
        client = TestClient(app, raise_server_exceptions=False)
        client.__enter__()
        self.addCleanup(client.__exit__, None, None, None)
        return client


@unittest.skipUnless(pg_available(), "PostgreSQL indisponível")
class HealthAndErrorsTest(ApiCase):
    def test_healthy(self):
        r = self.make_client().get("/health")
        self.assertEqual(r.status_code, 200)
        b = r.json()
        self.assertEqual((b["status"], b["database"]["ok"], b["broker"]["ok"]), ("ok", True, True))
        self.assertEqual(b["model"], {"loaded": True, "version": MODEL.version})

    def test_database_down_is_reported_unhealthy(self):
        c = self.make_client()
        self.db.pool.close()  # banco "cai"
        r = c.get("/health")
        self.assertEqual(r.status_code, 503)
        self.assertEqual((r.json()["status"], r.json()["database"]["ok"]), ("unhealthy", False))

    def test_broker_down_is_reported_unhealthy(self):
        c = self.make_client()
        self.broker["ok"] = False
        r = c.get("/health")
        self.assertEqual((r.status_code, r.json()["broker"]["ok"]), (503, False))

    def test_unexpected_errors_are_generic_without_internals(self):
        c = self.make_client()

        def boom(*a, **k):
            raise RuntimeError("senha=hunter2 /etc/segredo")

        self.db.stats = boom
        r = c.get("/stats")
        self.assertEqual(r.status_code, 500)
        self.assertEqual(list(r.json()), ["detail"])
        for leak in ("hunter2", "segredo", "Traceback", "RuntimeError"):
            self.assertNotIn(leak, r.text)

    def test_model_info(self):
        b = self.make_client().get("/model").json()
        self.assertEqual((b["version"], b["threshold"]), (MODEL.version, MODEL.threshold))
        for k in ("metrics", "comparison", "trained_at", "features", "threshold_criterion"):
            self.assertIn(k, b)
        self.assertIn("pr_auc", str(b["metrics"]).lower().replace("test_pr_auc", "pr_auc"))
        self.assertNotIn("reference", b)  # bins de drift não vão para o cliente
        opts = b["options"]
        self.assertIn({"name": "São Paulo", "country": "BR"}, opts["cities"])
        self.assertIn("crypto_exchange", opts["categories"])
        # tudo que o formulário oferece é aceito pelo /score
        c = self.make_client()
        for city in opts["cities"][:4]:
            self.assertEqual(c.post("/score", json={**VALID, "city": city["name"]}).status_code, 200)


@unittest.skipUnless(pg_available(), "PostgreSQL indisponível")
class ScoreEndpointTest(ApiCase):
    def test_valid_transaction_is_scored_and_not_persisted(self):
        c = self.make_client()
        r = c.post("/score", json=VALID)
        self.assertEqual(r.status_code, 200, r.text)
        b = r.json()
        self.assertTrue(0 <= b["score"] <= 1)
        self.assertEqual((b["threshold"], b["model_version"]), (MODEL.threshold, MODEL.version))
        self.assertEqual(len(b["top_features"]), 3)
        self.assertEqual(set(b["top_features"][0]), {"feature", "value", "contribution"})
        self.assertEqual(set(b["features"]), set(FEATURE_NAMES))
        self.assertEqual(b["flagged"], b["score"] >= b["threshold"])
        self.assertEqual(self.db.count(), 0)

    def test_invalid_payloads_get_clear_validation_errors_and_persist_nothing(self):
        c = self.make_client()
        bad = [
            {**VALID, "amount": 0}, {**VALID, "amount": -3}, {k: v for k, v in VALID.items() if k != "amount"},
            {**VALID, "city": "Atlantis"}, {**VALID, "merchant_category": "armas"}, {**VALID, "is_fraud": True},
            {**VALID, "context": {"segredo": 1}}, {**VALID, "context": {"is_new_device": 7}},
            {**VALID, "context": {"txn_count_1m": -1}}, {**VALID, "amount": "muito"},
        ]
        for payload in bad:
            r = c.post("/score", json=payload)
            self.assertEqual(r.status_code, 422, payload)
            self.assertIn("detail", r.json())
        self.assertEqual(self.db.count(), 0)

    def test_context_overrides_stateful_features_and_raises_the_score(self):
        c = self.make_client()
        calm = c.post("/score", json=VALID).json()
        risky = c.post("/score", json=SUSPICIOUS).json()
        self.assertGreater(risky["score"], calm["score"] + 0.5)
        self.assertTrue(risky["flagged"])
        self.assertEqual(risky["features"]["txn_count_1m"], 5.0)
        self.assertEqual(risky["features"]["is_new_device"], 1.0)
        self.assertGreater(risky["top_features"][0]["contribution"], 0)

    def test_uses_the_users_history_from_the_database(self):
        c = self.make_client()
        for i in range(4):
            self.db.insert_scored(scored(i, user="veterano", dt=i * 20))
        body = {**VALID, "user_id": "veterano", "timestamp": (T0 + timedelta(seconds=100)).isoformat()}
        f = c.post("/score", json=body).json()["features"]
        self.assertEqual((f["txn_count_1m"], f["txn_count_10m"], f["txn_count_30d"]), (2.0, 4.0, 4.0))  # aos +100 s: só os de +40 s e +60 s estão a ≤ 60 s
        cold = c.post("/score", json={**body, "user_id": "desconhecido"}).json()["features"]
        self.assertEqual(cold["txn_count_30d"], 0.0)
        self.assertEqual(self.db.count(), 4)  # nada novo persistido

    def test_oversized_body_is_rejected(self):
        c = self.make_client()
        r = c.post("/score", json={**VALID, "user_id": "x" * 6000})
        self.assertEqual(r.status_code, 413)


@unittest.skipUnless(pg_available(), "PostgreSQL indisponível")
class QueriesTest(ApiCase):
    def setUp(self):
        self.c = self.make_client()
        specs = [(0, 0.05, False, False), (1, 0.97, True, True), (2, 0.20, False, False), (3, 0.91, True, False),
                 (4, 0.03, False, True), (5, 0.99, True, True)]
        for i, score, flagged, fraud in specs:
            self.db.insert_scored(scored(i, score=score, flagged=flagged, fraud=fraud, latency=10.0 * (i + 1), dt=i))

    def test_recent_newest_first_limit_and_flagged_filter(self):
        items = self.c.get("/transactions?limit=4").json()["items"]
        self.assertEqual([t["transaction_id"] for t in items], ["t5", "t4", "t3", "t2"])
        flagged = self.c.get("/transactions?flagged_only=true").json()["items"]
        self.assertEqual([t["transaction_id"] for t in flagged], ["t5", "t3", "t1"])
        self.assertTrue(all(t["flagged"] for t in flagged))
        for k in ("score", "amount", "city", "merchant_category", "scored_at", "latency_ms", "is_fraud"):
            self.assertIn(k, items[0])

    def test_recent_validates_limit(self):
        self.assertEqual(self.c.get("/transactions?limit=0").status_code, 422)
        self.assertEqual(self.c.get("/transactions?limit=5000").status_code, 422)

    def test_stats_reflect_persisted_data(self):
        s = self.c.get("/stats?window_minutes=15").json()
        self.assertEqual((s["total"], s["flagged"]), (6, 3))
        self.assertAlmostEqual(s["flag_rate"], 0.5)
        self.assertEqual(sum(s["score_histogram"]), 6)
        self.assertEqual(s["score_histogram"][0], 2)  # 0.05 e 0.03
        self.assertEqual(s["score_histogram"][9], 3)  # 0.97, 0.91, 0.99
        self.assertAlmostEqual(s["latency_ms"]["p50"], 35.0)
        self.assertEqual(s["last_minute"], 6)
        lab = s["labeled"]
        self.assertEqual((lab["tp"], lab["fp"], lab["fn"], lab["tn"]), (2, 1, 1, 2))
        self.assertAlmostEqual(lab["precision"], 2 / 3)
        self.assertAlmostEqual(lab["recall"], 2 / 3)
        self.assertEqual(sum(p["count"] for p in s["timeline"]), 6)

    def test_stats_window_excludes_old_rows(self):
        with self.db.pool.connection() as conn:
            conn.execute("UPDATE transactions SET scored_at = now() - interval '3 hours' WHERE transaction_id IN ('t0','t1')")
        self.assertEqual(self.c.get("/stats?window_minutes=15").json()["total"], 4)
        self.assertEqual(self.c.get("/stats?window_minutes=1440").json()["total"], 6)
        self.assertEqual(self.c.get("/stats?window_minutes=0").status_code, 422)


def _rows(seed, n=2500):
    """(transações, matriz de features) de uma simulação independente — o 'tráfego ao vivo' dos testes de drift.
    2500 eventos ≈ 3-4 dias simulados: a janela de drift precisa cobrir vários ciclos diurnos
    (senão hour_of_day desloca só por sazonalidade — foi o que a 1ª versão deste teste, com 500, mostrou)."""
    txns = Simulator(SimConfig(n_users=150, seed=seed)).advance(45)
    ds = build_dataset(txns)
    return txns[-n:], ds.X[-n:]


@unittest.skipUnless(pg_available(), "PostgreSQL indisponível")
class DriftEndpointTest(ApiCase):
    @classmethod
    def setUpClass(cls):
        cls.txns, cls.X = _rows(seed=21)

    def _insert(self, X):
        scores = MODEL.predict(X)
        for t, x, s in zip(self.txns, X, scores):
            self.db.insert_scored(ScoredTransaction(t, [float(v) for v in x], float(s), MODEL.is_flagged(float(s)), MODEL.threshold, MODEL.version, 15.0))

    def test_too_few_samples_is_reported_as_insufficient(self):
        c = self.make_client()
        self.db.insert_scored(scored(1))
        r = c.get("/drift").json()
        self.assertEqual((r["status"], r["n"], r["alerts"]), ("insufficient_data", 1, []))

    def test_traffic_from_the_training_distribution_stays_below_the_limit(self):
        c = self.make_client()
        self._insert(self.X)
        r = c.get("/drift").json()
        self.assertEqual((r["status"], r["alerts"]), ("ok", []), r["features"])
        self.assertLess(max(r["features"].values()), 0.2)
        self.assertLess(r["score"], 0.2)

    def test_shifted_feature_raises_an_alert(self):
        c = self.make_client()
        shifted = self.X.copy()
        shifted[:, FEATURE_NAMES.index("amount")] *= 4
        self._insert(shifted)
        r = c.get("/drift").json()
        self.assertEqual(r["status"], "alert")
        self.assertIn("amount", r["alerts"])
        self.assertGreater(r["features"]["amount"], 0.2)
        self.assertLess(r["features"]["hour_of_day"], 0.2)  # o que não mudou continua estável


@unittest.skipUnless(pg_available(), "PostgreSQL indisponível")
class StreamTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = make_db()
        self.addAsyncCleanup(asyncio.to_thread, self.db.drop_schema)

    async def _collect(self, gen, n, timeout=5):
        out = []

        async def read():
            async for chunk in gen:
                out.append(chunk)
                if len(out) >= n:
                    return

        await asyncio.wait_for(read(), timeout)
        return out

    async def test_new_transaction_reaches_a_connected_client(self):
        b = Broadcaster(self.db, poll_interval=0.05)
        await b.start()
        self.addAsyncCleanup(b.stop)
        q = b.subscribe()
        stop = {"v": False}

        async def disconnected():
            return stop["v"]

        gen = sse_events(disconnected, b, q, heartbeat=5)
        first = await self._collect(gen, 1)
        self.assertIn("event: ready", first[0])
        await asyncio.to_thread(self.db.insert_scored, scored(1, score=0.9, flagged=True))
        chunk = (await self._collect(gen, 1))[0]
        self.assertTrue(chunk.startswith("event: transaction\ndata: "))
        self.assertIn('"transaction_id": "t1"', chunk)
        # cliente desconecta → o gerador termina e a inscrição é removida
        stop["v"] = True
        await asyncio.wait_for(gen.aclose(), 2)
        self.assertEqual(b.clients, 0)

    async def test_only_new_rows_are_streamed_and_heartbeat_keeps_the_connection_alive(self):
        await asyncio.to_thread(self.db.insert_scored, scored(0))  # já existia antes de conectar
        b = Broadcaster(self.db, poll_interval=0.05)
        await b.start()
        self.addAsyncCleanup(b.stop)
        q = b.subscribe()

        async def never():
            return False

        gen = sse_events(never, b, q, heartbeat=0.2)
        chunks = await self._collect(gen, 3)  # ready + 2 heartbeats: a linha antiga NÃO aparece
        self.assertIn("event: ready", chunks[0])
        self.assertEqual(chunks[1:], [": ping\n\n", ": ping\n\n"])
        await gen.aclose()

    async def test_slow_client_loses_oldest_and_client_cap_is_enforced(self):
        b = Broadcaster(self.db, poll_interval=0.05, queue_size=3, max_clients=1)
        await b.start()
        self.addAsyncCleanup(b.stop)
        q = b.subscribe()
        self.assertIsNone(b.subscribe())  # teto de clientes
        for i in range(6):
            await asyncio.to_thread(self.db.insert_scored, scored(i))
        await asyncio.sleep(0.5)
        self.assertEqual([q.get_nowait()["transaction_id"] for _ in range(q.qsize())], ["t3", "t4", "t5"])


@unittest.skipUnless(pg_available(), "PostgreSQL indisponível")
class StreamEndpointTest(ApiCase):
    def test_returns_503_when_too_many_clients(self):
        c = self.make_client()
        c.app.state.broadcaster.max_clients = 0
        self.assertEqual(c.get("/stream").status_code, 503)


class RateLimiterTest(unittest.TestCase):
    class Clock:
        t = 1000.0

        def __call__(self):
            return self.t

    def test_each_visitor_has_an_independent_bucket(self):
        clock = self.Clock()
        rl = RateLimiter(3, 100, clock=clock)
        self.assertEqual([rl.allow("a")[0] for _ in range(4)], [True, True, True, False])
        self.assertTrue(rl.allow("b")[0])  # outro visitante segue atendido

    def test_window_slides(self):
        clock = self.Clock()
        rl = RateLimiter(2, 100, clock=clock)
        rl.allow("a"), rl.allow("a")
        ok, retry = rl.allow("a")
        self.assertFalse(ok)
        self.assertGreaterEqual(retry, 1.0)
        clock.t += 61
        self.assertTrue(rl.allow("a")[0])

    def test_global_cap_protects_against_many_visitors(self):
        rl = RateLimiter(5, 4, clock=self.Clock())
        self.assertEqual([rl.allow(f"v{i}")[0] for i in range(6)], [True] * 4 + [False] * 2)

    def test_memory_is_bounded(self):
        from app.api import rate_limit

        clock = self.Clock()
        rl = RateLimiter(5, 10**9, clock=clock)
        for i in range(rate_limit.MAX_TRACKED_VISITORS + 50):
            rl.allow(f"v{i}")
            clock.t += 0.01
        clock.t += 120  # todos expiram
        rl.allow("novo")
        self.assertLess(len(rl._hits), rate_limit.MAX_TRACKED_VISITORS)


@unittest.skipUnless(pg_available(), "PostgreSQL indisponível")
class RateLimitHttpTest(ApiCase):
    def test_excess_gets_429_for_that_visitor_only(self):
        c = self.make_client(score_rate_limit_per_minute=2)
        cf = lambda ip: {"CF-Connecting-IP": ip}
        self.assertEqual([c.post("/score", json=VALID, headers=cf("1.1.1.1")).status_code for _ in range(3)], [200, 200, 429])
        r = c.post("/score", json=VALID, headers=cf("1.1.1.1"))
        self.assertIn("Retry-After", r.headers)
        self.assertEqual(c.post("/score", json=VALID, headers=cf("2.2.2.2")).status_code, 200)  # outro visitante
        # sem CF-Connecting-IP: primeiro X-Forwarded-For; e depois o peer
        xff = lambda ip: {"X-Forwarded-For": f"{ip}, 10.0.0.1"}
        self.assertEqual([c.post("/score", json=VALID, headers=xff("3.3.3.3")).status_code for _ in range(3)], [200, 200, 429])
        self.assertEqual(c.post("/score", json=VALID, headers=xff("4.4.4.4")).status_code, 200)

    def test_reads_are_not_rate_limited_by_the_score_limiter(self):
        c = self.make_client(score_rate_limit_per_minute=1)
        self.assertTrue(all(c.get("/stats").status_code == 200 for _ in range(5)))


class StaticAndPrefixTest(ApiCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        d = Path(cls._tmp.name)
        (d / "index.html").write_text('<link rel="stylesheet" href="style.css"><script src="app.js"></script>')
        (d / "style.css").write_text("body{}")
        (d / "app.js").write_text("fetch('health')")
        cls.static_dir = d

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    @unittest.skipUnless(pg_available(), "PostgreSQL indisponível")
    def test_frontend_is_served_by_the_api_and_api_routes_win_over_the_mount(self):
        c = self.make_client()
        r = c.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r.headers["content-type"])
        self.assertEqual(c.get("/style.css").status_code, 200)
        self.assertEqual(c.get("/health").json()["status"], "ok")  # rota da API, não arquivo estático

    @unittest.skipUnless(pg_available(), "PostgreSQL indisponível")
    def test_works_behind_a_prefix_stripped_by_the_proxy(self):
        """Simula o Nginx: location /projects/fraud/ + proxy_pass .../ remove o prefixo."""
        c = self.make_client()
        inner = c.app

        async def strip_prefix(scope, receive, send):
            if scope["type"] in ("http", "websocket") and scope["path"].startswith("/projects/fraud/"):
                scope = {**scope, "path": scope["path"][len("/projects/fraud"):], "raw_path": scope["path"][len("/projects/fraud"):].encode()}
            await inner(scope, receive, send)

        proxied = TestClient(strip_prefix, raise_server_exceptions=False)
        self.assertEqual(proxied.get("/projects/fraud/").status_code, 200)
        self.assertEqual(proxied.get("/projects/fraud/style.css").status_code, 200)
        self.assertEqual(proxied.get("/projects/fraud/health").json()["status"], "ok")
        self.assertEqual(proxied.post("/projects/fraud/score", json=VALID).status_code, 200)

    def test_real_frontend_uses_only_relative_urls(self):
        """Uma URL raiz-absoluta ('/health', href='/x') quebraria atrás do prefixo /projects/fraud/."""
        static = Path(__file__).resolve().parents[1] / "app" / "static"
        offenders = []
        for f in static.glob("*"):
            if f.suffix not in (".html", ".js", ".css"):
                continue
            text = f.read_text()
            # <a href="/..."> leva ao site principal (mesmo domínio) e é intencional; assets, API e stream têm de ser relativos.
            text = re.sub(r"<a\s[^>]*>", "", text)
            for m in re.finditer(r"""(?:href|src|action)\s*=\s*["']/(?!/)|fetch\(\s*["'`]/|EventSource\(\s*["'`]/|url\(\s*["']?/(?!/)""", text):
                offenders.append((f.name, m.group(0)))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
