"""Treina e avalia o modelo de fraude, grava o artefato em models/ e o relatório em reports/.

    python3 scripts/train.py                     # config padrão (2000 usuários x 60 dias, seed 42)
    python3 scripts/train.py --users 500 --days 30 --n-estimators 200   # rodada rápida

Usa dados 100% sintéticos regenerados pela seed (não depende de arquivos em data/).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.model.training import TrainConfig, run_training, render_markdown  # noqa: E402


def main() -> None:
    d = TrainConfig()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--users", type=int, default=d.users)
    ap.add_argument("--days", type=int, default=d.days)
    ap.add_argument("--seed", type=int, default=d.seed)
    ap.add_argument("--fraud-rate", type=float, default=d.fraud_rate)
    ap.add_argument("--max-fpr", type=float, default=d.max_fpr, help="teto de FPR para escolher o threshold (na validação)")
    ap.add_argument("--n-estimators", type=int, default=d.n_estimators)
    ap.add_argument("--n-jobs", type=int, default=d.n_jobs)
    ap.add_argument("--model-dir", type=Path, default=Path("models"))
    ap.add_argument("--reports-dir", type=Path, default=Path("reports"))
    a = ap.parse_args()

    cfg = TrainConfig(users=a.users, days=a.days, seed=a.seed, fraud_rate=a.fraud_rate, max_fpr=a.max_fpr,
                      n_estimators=a.n_estimators, n_jobs=a.n_jobs)
    result = run_training(cfg, a.model_dir, a.reports_dir)
    print()
    print(render_markdown(result))
    print(f"concluído em {result['seconds']}s")


if __name__ == "__main__":
    main()
