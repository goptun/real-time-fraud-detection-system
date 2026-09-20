"""Gera o dataset sintético rotulado e imprime um resumo.

    python3 scripts/generate_data.py                       # 2000 usuários x 60 dias
    python3 scripts/generate_data.py --users 500 --days 30 --seed 7 --out data/small.jsonl.gz

Os dados são 100% sintéticos (nenhum dado pessoal ou financeiro real). O treino
NÃO depende deste arquivo — `scripts/train.py` regenera o dataset a partir da
seed —, mas exportá-lo é útil para inspeção.
"""

from __future__ import annotations

import argparse
import collections
import gzip
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.data.simulator import SimConfig, Simulator  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--users", type=int, default=2000)
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--fraud-rate", type=float, default=0.02)
    ap.add_argument("--out", type=Path, default=Path("data/transactions.jsonl.gz"))
    args = ap.parse_args()

    t0 = time.time()
    sim = Simulator(SimConfig(n_users=args.users, seed=args.seed, fraud_rate=args.fraud_rate))
    txns = sim.advance(args.days)
    gen_s = time.time() - t0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(args.out, "wt", encoding="utf-8") as f:
        for t in txns:
            row = t.to_event(with_label=True)
            row["fraud_pattern"] = t.fraud_pattern
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    frauds = [t for t in txns if t.is_fraud]
    legit_amounts = [t.amount for t in txns if not t.is_fraud]
    print(f"seed={args.seed} users={args.users} days={args.days}  (gerado em {gen_s:.1f}s)")
    print(f"período: {txns[0].timestamp:%Y-%m-%d} -> {txns[-1].timestamp:%Y-%m-%d}")
    print(f"transações: {len(txns):,}  ({len(txns) / args.users / args.days:.1f} por usuário/dia)")
    print(f"fraudes: {len(frauds):,}  taxa = {len(frauds) / len(txns):.2%}")
    print("por padrão de fraude:")
    for pattern, n in collections.Counter(t.fraud_pattern for t in frauds).most_common():
        print(f"  {pattern:<18} {n:>6,}  ({n / len(frauds):.1%})")
    print(f"valor legítimo: mediana R$ {statistics.median(legit_amounts):.2f}, p99 R$ {sorted(legit_amounts)[int(len(legit_amounts) * .99)]:.2f}")
    print(f"arquivo: {args.out} ({args.out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
