"""Utilidades de Kafka compartilhadas (produtor, testes, scripts)."""

from __future__ import annotations

import json

from confluent_kafka import Producer

from app.data.schema import Transaction


def make_producer(bootstrap_servers: str) -> Producer:
    return Producer(
        {
            "bootstrap.servers": bootstrap_servers,
            "linger.ms": 20,
            "enable.idempotence": True,  # sem duplicatas por retry do próprio produtor
            "acks": "all",
        }
    )


def publish_transaction(producer: Producer, topic: str, txn: Transaction, *, with_label: bool = False) -> None:
    """Chave = user_id: todas as transações de um usuário caem na mesma partição, em ordem —
    pré-requisito para as features stateful por usuário estarem corretas."""
    producer.produce(topic, key=txn.user_id.encode(), value=json.dumps(txn.to_event(with_label=with_label), ensure_ascii=False).encode())
