"""Reavalia o artefato salvo em models/ e confere contra as métricas gravadas no meta.json.

    python3 scripts/evaluate.py [--model-dir models] [--tol 0.005]

Regenera o dataset pela seed do artefato, refaz o mesmo split temporal e recalcula
PR-AUC/precision/recall/F1/FPR do modelo no threshold do artefato. Sai com código 1
se algum número divergir além da tolerância (reprodutibilidade dos resultados do README).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.model.training import evaluate_saved_model  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model-dir", type=Path, default=Path("models"))
    ap.add_argument("--tol", type=float, default=0.005, help="tolerância absoluta por métrica")
    a = ap.parse_args()

    fresh, stored = evaluate_saved_model(a.model_dir)
    rows = [("test_pr_auc", fresh["test_pr_auc"], stored["test_pr_auc"])] + [
        (k, fresh["test"][k], stored["test"][k]) for k in ("precision", "recall", "f1", "fpr")
    ]
    ok = True
    print(f"{'métrica':<14}{'recalculado':>13}{'no artefato':>13}{'|Δ|':>10}")
    for name, new, old in rows:
        delta = abs(new - old)
        ok &= delta <= a.tol
        print(f"{name:<14}{new:>13.4f}{old:>13.4f}{delta:>10.5f}{'' if delta <= a.tol else '   <-- FORA DA TOLERÂNCIA'}")
    print("OK: resultados reproduzidos" if ok else "FALHA: resultados divergem do artefato")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
