import json
import threading
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone

from confluent_kafka import Consumer, Producer
from confluent_kafka.admin import AdminClient, NewTopic

from app.config.settings import Settings, get_settings
from app.data.schema import Transaction
from app.features.engine import FEATURE_NAMES
from app.model.artifact import FraudModel
from app.storage.db import ScoredTransaction
from app.streaming.kafka_utils import make_producer, publish_transaction
from app.streaming.scorer import DUPLICATE, REJECTED, SCORED, ScoringService
from tests.pg import make_db, pg_available

BOOTSTRAP = "127.0.0.1:29092"
T0 = datetime(2026, 3, 1, 12, tzinfo=timezone.utc)


def kafka_available() -> bool:
    try:
        AdminClient({"bootstrap.servers": BOOTSTRAP}).list_topics(timeout=3)
        return True
    except Exception:
        return False


def mk(i, user="u1", dt=0.0, **over):
    p = dict(
        transaction_id=f"t{i}", user_id=user, amount=50.0, country="BR", city="São Paulo", device_id="d1",
        merchant_id="m_000", merchant_category="groceries", timestamp=T0 + timedelta(seconds=dt),
    )
    p.update(over)
    return Transaction(**p)


def settings() -> Settings:
    return Settings(_env_file=None, kafka_bootstrap_servers=BOOTSTRAP)


MODEL = FraudModel.load(get_settings().model_dir)


@unittest.skipUnless(pg_available(), "PostgreSQL indisponível")
class ProcessMessageTest(unittest.TestCase):
    """Lógica de uma mensagem, sem broker."""

    def setUp(self):
        self.db = make_db()
        self.addCleanup(self.db.drop_schema)
        self.svc = ScoringService(MODEL, self.db, settings())

    def test_valid_message_is_scored_and_persisted(self):
        out, reason = self.svc.process(json.dumps(mk(1).to_event()), time.time() * 1000 - 40)
        self.assertEqual((out, reason), (SCORED, None))
        with self.db.pool.connection() as c:
            score, ver, latency, feats = c.execute("SELECT score, model_version, latency_ms, features FROM transactions").fetchone()
        self.assertTrue(0 <= score <= 1)
        self.assertEqual(ver, MODEL.version)
        self.assertGreaterEqual(latency, 40)
        self.assertEqual(len(feats), len(FEATURE_NAMES))

    def test_state_accumulates_per_user_across_messages(self):
        for i in range(3):
            self.svc.process(json.dumps(mk(i, dt=i * 10).to_event()))
        with self.db.pool.connection() as c:
            counts = [r[0] for r in c.execute("SELECT features[%s] FROM transactions ORDER BY event_time", (FEATURE_NAMES.index("txn_count_1m") + 1,)).fetchall()]
        self.assertEqual(counts, [0.0, 1.0, 2.0])  # a frequência reflete as transações anteriores

    def test_duplicate_delivery_yields_single_record_and_single_state_update(self):
        raw = json.dumps(mk(1).to_event())
        self.assertEqual(self.svc.process(raw)[0], SCORED)
        self.assertEqual(self.svc.process(raw)[0], DUPLICATE)
        self.assertEqual(self.db.count(), 1)
        self.assertEqual(self.svc.store.get("u1").n, 1)  # state não contou duas vezes

    def test_flagged_follows_the_model_threshold(self):
        # rajada de dispositivo novo + cidade distante + categoria de risco → score alto
        base = [mk(i, dt=i * 3600, merchant_id="m_001") for i in range(5)]
        for t in base:
            self.svc.process(json.dumps(t.to_event()))
        bad = mk(99, dt=5 * 3600 + 60, device_id="dev_new", city="Tokyo", country="JP", amount=9000.0,
                 merchant_id="m_120", merchant_category="crypto_exchange")
        self.svc.process(json.dumps(bad.to_event()))
        with self.db.pool.connection() as c:
            score, flagged, thr = c.execute("SELECT score, flagged, threshold FROM transactions WHERE transaction_id='t99'").fetchone()
        self.assertEqual(flagged, score >= thr)
        self.assertTrue(flagged, score)

    def test_malformed_and_invalid_messages_are_rejected_with_reason(self):
        cases = {
            b"{not json": "JSON inválido",
            b"\xff\xfe": "JSON inválido",
            b"[1,2,3]": "não é um objeto",
            json.dumps({"transaction_id": "x"}).encode(): "schema inválido",
            json.dumps({**mk(1).to_event(), "amount": -5}).encode(): "amount",
            json.dumps({**mk(1).to_event(), "city": "Atlantis"}).encode(): "schema inválido",
            None: "JSON inválido",
        }
        for raw, expected in cases.items():
            out, reason = self.svc.process(raw)
            self.assertEqual(out, REJECTED, raw)
            self.assertIn(expected, reason)
        self.assertEqual(self.db.count(), 0)

    def test_state_is_rebuilt_from_the_database_after_restart(self):
        for i in range(4):
            self.svc.process(json.dumps(mk(i, dt=i * 30).to_event()))
        fresh = ScoringService(MODEL, self.db, settings())  # "reinício": cache vazio
        fresh.process(json.dumps(mk(10, dt=200).to_event()))
        self.assertEqual(fresh.store.loads, 1)
        with self.db.pool.connection() as c:
            n = c.execute("SELECT features[%s] FROM transactions WHERE transaction_id='t10'", (FEATURE_NAMES.index("txn_count_30d") + 1,)).fetchone()[0]
        self.assertEqual(n, 4.0)  # histórico veio do banco


@unittest.skipUnless(pg_available() and kafka_available(), "Kafka/PostgreSQL indisponíveis (docker compose ... up -d)")
class KafkaIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.db = make_db()
        self.addCleanup(self.db.drop_schema)
        tag = uuid.uuid4().hex[:8]
        self.topic, self.dlq, self.group = f"test-tx-{tag}", f"test-rej-{tag}", f"test-grp-{tag}"
        self.admin = AdminClient({"bootstrap.servers": BOOTSTRAP})
        for f in self.admin.create_topics([NewTopic(self.topic, 3, 1), NewTopic(self.dlq, 1, 1)]).values():
            f.result(10)
        self.addCleanup(lambda: self.admin.delete_topics([self.topic, self.dlq]))
        self.producer = make_producer(BOOTSTRAP)

    def _run_service(self, expect_rows, timeout=45):
        svc = ScoringService(MODEL, self.db, settings(), topic=self.topic, dlq_topic=self.dlq, group_id=self.group)
        stop = threading.Event()
        th = threading.Thread(target=svc.run, args=(stop,), daemon=True)
        th.start()
        deadline = time.time() + timeout
        while time.time() < deadline and self.db.count() < expect_rows:
            time.sleep(0.3)
        time.sleep(1.5)  # deixa passar mensagens restantes (duplicadas/rejeitadas) e o commit
        stop.set()
        th.join(15)
        return svc

    def _read_dlq(self):
        c = Consumer({"bootstrap.servers": BOOTSTRAP, "group.id": f"dlq-{uuid.uuid4().hex[:6]}", "auto.offset.reset": "earliest"})
        c.subscribe([self.dlq])
        out, end = [], time.time() + 10
        while time.time() < end:
            m = c.poll(1.0)
            if m is not None and not m.error():
                out.append(json.loads(m.value()))
            elif out:
                break
        c.close()
        return out

    def test_valid_duplicate_and_malformed_events_end_to_end(self):
        valid = [mk(i, user=f"u{i % 3}", dt=i) for i in range(12)]
        for t in valid:
            publish_transaction(self.producer, self.topic, t)
        publish_transaction(self.producer, self.topic, valid[0])  # duplicata
        self.producer.produce(self.topic, key=b"u0", value=b"{quebrado")
        self.producer.produce(self.topic, key=b"u1", value=json.dumps({"transaction_id": "sem-campos"}).encode())
        publish_transaction(self.producer, self.topic, mk(100, user="u0", dt=500))  # válido DEPOIS dos inválidos
        self.producer.flush(10)

        svc = self._run_service(expect_rows=13)
        self.assertEqual(self.db.count(), 13)  # 12 + o válido pós-inválidos; duplicata não entra
        self.assertEqual((svc.counters.scored, svc.counters.duplicates, svc.counters.rejected), (13, 1, 2))
        dead = self._read_dlq()
        self.assertEqual(len(dead), 2)
        self.assertTrue(all(d["reason"] and "source" in d for d in dead))

        # offsets commitados: um novo consumidor do mesmo grupo não recebe nada de volta
        c = Consumer({"bootstrap.servers": BOOTSTRAP, "group.id": self.group, "auto.offset.reset": "earliest"})
        c.subscribe([self.topic])
        got = [m for m in (c.poll(2.0) for _ in range(4)) if m is not None and not m.error()]
        c.close()
        self.assertEqual(got, [])

    def test_restart_resumes_without_loss_or_duplicates(self):
        first = [mk(i, user=f"u{i % 4}", dt=i) for i in range(20)]
        for t in first:
            publish_transaction(self.producer, self.topic, t)
        self.producer.flush(10)
        self._run_service(expect_rows=20)
        second = [mk(i, user=f"u{i % 4}", dt=i) for i in range(20, 40)]
        for t in second:
            publish_transaction(self.producer, self.topic, t)
        self.producer.flush(10)
        svc2 = self._run_service(expect_rows=40)  # novo serviço = reinício, cache vazio
        self.assertEqual(self.db.count(), 40)
        self.assertEqual(svc2.counters.scored, 20)  # só as novas; as 20 primeiras não foram reprocessadas
        with self.db.pool.connection() as c:
            distinct = c.execute("SELECT count(DISTINCT transaction_id) FROM transactions").fetchone()[0]
        self.assertEqual(distinct, 40)


if __name__ == "__main__":
    unittest.main()
