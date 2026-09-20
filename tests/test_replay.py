import json
import threading
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone

from confluent_kafka import Consumer
from confluent_kafka.admin import AdminClient, NewTopic

from app.config.settings import Settings
from app.data.schema import Transaction
from app.streaming.replay import Pacer, ReplayProducer, apply_drift, effective_rate, resolve_start
from tests.pg import make_db, pg_available
from tests.test_scorer import BOOTSTRAP, kafka_available, mk

T0 = datetime(2026, 3, 1, 12, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self):
        self.t = 100.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


class PacerTest(unittest.TestCase):
    def test_paces_at_the_configured_rate(self):
        clock = FakeClock()
        p = Pacer(5.0, clock.now, clock.sleep)
        start = clock.t
        for _ in range(11):
            p.wait()
        self.assertAlmostEqual(clock.t - start, 2.0, places=6)  # 11 eventos a 5/s: 10 intervalos de 0,2 s

    def test_does_not_burst_to_catch_up(self):
        clock = FakeClock()
        p = Pacer(10.0, clock.now, clock.sleep)
        p.wait()
        clock.t += 5.0  # travou por 5 s
        before = clock.t
        for _ in range(3):
            p.wait()
        self.assertGreaterEqual(clock.t - before, 0.15)  # continua espaçado, sem rajada de "recuperação"

    def test_rejects_non_positive_rate(self):
        with self.assertRaises(ValueError):
            Pacer(0)

    def test_hard_cap_overrides_configured_rate(self):
        s = Settings(_env_file=None, replay_rate_per_sec=500.0, replay_max_rate_per_sec=20.0)
        self.assertEqual(effective_rate(s), 20.0)
        self.assertEqual(effective_rate(Settings(_env_file=None, replay_rate_per_sec=3.0)), 3.0)


class DriftTest(unittest.TestCase):
    def test_amount_drift_shifts_the_distribution(self):
        txns = [mk(i, amount=100.0 + i) for i in range(50)]
        shifted = [apply_drift(t, "amount", 3.0) for t in txns]
        self.assertAlmostEqual(sum(t.amount for t in shifted) / sum(t.amount for t in txns), 3.0, places=2)

    def test_no_drift_is_identity(self):
        t = mk(1, amount=42.0)
        self.assertIs(apply_drift(t, "", 3.0), t)
        self.assertIs(apply_drift(t, "amount", 1.0), t)

    def test_unsupported_feature_fails_loudly(self):
        with self.assertRaises(ValueError):
            apply_drift(mk(1), "km_from_last", 2.0)


@unittest.skipUnless(pg_available(), "PostgreSQL indisponível")
class ResolveStartTest(unittest.TestCase):
    def setUp(self):
        self.db = make_db()
        self.addCleanup(self.db.drop_schema)

    def test_empty_database_needs_warmup_and_starts_now(self):
        self.assertEqual(resolve_start(self.db, T0), (T0, True))

    def test_never_starts_before_the_last_persisted_event(self):
        from app.storage.db import ScoredTransaction

        future = T0 + timedelta(days=3)  # o relógio acelerado da execução anterior foi além do "agora"
        self.db.insert_scored(ScoredTransaction(mk(1, dt=3 * 86400), [0.0], 0.1, False, 0.5, "v"))
        start, warmup = resolve_start(self.db, T0)
        self.assertEqual((start, warmup), (future + timedelta(seconds=1), False))
        self.assertEqual(resolve_start(self.db, future + timedelta(days=1))[0], future + timedelta(days=1))


class ReplayEventsTest(unittest.TestCase):
    def _producer(self, **kw):
        return ReplayProducer(Settings(_env_file=None, replay_users=40, replay_seed=3, **kw), db=None, producer=object(), run_id="abc")

    def test_warmup_precedes_live_and_ids_carry_the_run_prefix(self):
        rp = self._producer(replay_warmup_days=3)
        it = rp.events(T0, warmup=True)
        seen = [next(it) for _ in range(400)]
        self.assertTrue(seen[0][1])  # começa em warm-up
        flags = [w for _, w in seen]
        self.assertEqual(flags, sorted(flags, reverse=True))  # todo warm-up antes de todo o "ao vivo"
        self.assertTrue(all(t.transaction_id.startswith("rabc_") for t, _ in seen))
        stamps = [t.timestamp for t, _ in seen]
        self.assertEqual(stamps, sorted(stamps))

    def test_live_stream_without_warmup_starts_at_the_given_clock(self):
        rp = self._producer()
        first = next(rp.events(T0, warmup=False))[0]
        self.assertGreaterEqual(first.timestamp, T0)
        self.assertFalse(next(rp.events(T0, warmup=False))[1])

    def test_events_are_infinite_and_time_keeps_advancing(self):
        rp = self._producer(replay_warmup_days=1)
        it = rp.events(T0, warmup=False)
        last = None
        for _ in range(1500):  # bem mais que 1 dia simulado de 40 usuários
            t, _ = next(it)
            if last is not None:
                self.assertGreaterEqual(t.timestamp, last)
            last = t.timestamp
        self.assertGreater(last, T0 + timedelta(days=2))


@unittest.skipUnless(kafka_available(), "Kafka indisponível")
class ReplayKafkaTest(unittest.TestCase):
    def test_publishes_labelled_events_keyed_by_user_at_the_configured_rate(self):
        tag = uuid.uuid4().hex[:8]
        topic = f"test-replay-{tag}"
        admin = AdminClient({"bootstrap.servers": BOOTSTRAP})
        admin.create_topics([NewTopic(topic, 3, 1)])[topic].result(10)
        self.addCleanup(lambda: admin.delete_topics([topic]))

        s = Settings(_env_file=None, kafka_bootstrap_servers=BOOTSTRAP, kafka_topic_transactions=topic,
                     replay_users=30, replay_rate_per_sec=40.0, replay_warmup_days=1, replay_warmup_rate_per_sec=1000.0)
        rp = ReplayProducer(s, db=None)
        stop = threading.Event()
        th = threading.Thread(target=rp.run, args=(stop,), daemon=True)
        t0 = time.monotonic()
        th.start()
        time.sleep(4)
        stop.set()
        th.join(15)
        elapsed = time.monotonic() - t0
        self.assertGreater(rp.published, 100)

        c = Consumer({"bootstrap.servers": BOOTSTRAP, "group.id": f"g-{tag}", "auto.offset.reset": "earliest"})
        c.subscribe([topic])
        msgs, idle = [], 0
        while idle < 4 and len(msgs) < rp.published:
            m = c.poll(1.0)
            if m is None or m.error():
                idle += 1
            else:
                msgs.append(m)
        c.close()
        self.assertEqual(len(msgs), rp.published)
        by_partition_user = {}
        for m in msgs:
            payload = json.loads(m.value())
            Transaction.model_validate(payload)  # eventos válidos no schema
            self.assertEqual(m.key().decode(), payload["user_id"])
            self.assertIn("is_fraud", payload)  # replay sintético carrega a verdade-terreno
            by_partition_user.setdefault(payload["user_id"], set()).add(m.partition())
        self.assertTrue(all(len(p) == 1 for p in by_partition_user.values()))  # usuário → sempre a mesma partição
        live_events = rp.published - 30 * 5  # descontando o warm-up (rápido)
        self.assertLess(live_events / elapsed, 60)  # e o ao-vivo respeita ~40/s (teto folgado p/ jitter)


if __name__ == "__main__":
    unittest.main()
