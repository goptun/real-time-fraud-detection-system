"""Pipeline de treino e avaliação: dados sintéticos → features (mesma máquina
de estados do serving) → split temporal → candidatos → threshold na validação →
avaliação no teste → artefato + relatório.

Candidatos: regra de valor (baseline), regressão logística, XGBoost e LightGBM
(cada um com/sem peso de classe). O modelo servido é o GBM de maior PR-AUC de
VALIDAÇÃO; o teste só reporta.
"""

from __future__ import annotations

import json
import time
import warnings
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np

from app.data.simulator import SimConfig, Simulator
from app.features.dataset import FeatureDataset, build_dataset
from app.features.engine import FEATURE_NAMES
from app.model.artifact import FraudModel, save_artifact
from app.model.baselines import amount_rule_scorer, fit_logistic_regression
from app.model.drift import make_reference
from app.model.metrics import evaluate_candidate, precision_recall_points
from app.model.split import temporal_split

ScoreFn = Callable[[np.ndarray], np.ndarray]


@dataclass
class TrainConfig:
    users: int = 2000
    days: int = 60
    seed: int = 42
    fraud_rate: float = 0.02
    max_fpr: float = 0.01
    n_estimators: int = 600
    early_stopping_rounds: int = 40
    n_jobs: int = 4
    version_tag: str = "v1"


def _fit_xgboost(Xtr, ytr, Xva, yva, cfg: TrainConfig, weighted: bool):
    import xgboost as xgb

    spw = float((ytr == 0).sum() / max((ytr == 1).sum(), 1)) if weighted else 1.0
    clf = xgb.XGBClassifier(
        n_estimators=cfg.n_estimators, learning_rate=0.05, max_depth=6, min_child_weight=5,
        subsample=0.8, colsample_bytree=0.8, reg_lambda=1.0, tree_method="hist",
        objective="binary:logistic", eval_metric="aucpr", early_stopping_rounds=cfg.early_stopping_rounds,
        scale_pos_weight=spw, random_state=cfg.seed, n_jobs=cfg.n_jobs,
    )
    clf.fit(Xtr, ytr, eval_set=[(Xva, yva)], verbose=False)
    booster = clf.get_booster()
    return (lambda X: clf.predict_proba(X)[:, 1]), booster, {"best_iteration": int(clf.best_iteration), "num_iteration": int(clf.best_iteration) + 1, "scale_pos_weight": spw}


def _fit_lightgbm(Xtr, ytr, Xva, yva, cfg: TrainConfig, weighted: bool):
    import lightgbm as lgb

    spw = float((ytr == 0).sum() / max((ytr == 1).sum(), 1)) if weighted else 1.0
    clf = lgb.LGBMClassifier(
        n_estimators=cfg.n_estimators, learning_rate=0.05, num_leaves=63, min_child_samples=40,
        subsample=0.8, subsample_freq=1, colsample_bytree=0.8, scale_pos_weight=spw,
        random_state=cfg.seed, n_jobs=cfg.n_jobs, verbose=-1,
    )
    with warnings.catch_warnings():
        # `eval_set` foi deprecado na 4.7 em favor de eval_X/eval_y, que não existem na 4.3 (piso do requirements).
        warnings.filterwarnings("ignore", message=".*eval_set.*deprecated.*")
        clf.fit(
            Xtr, ytr, eval_set=[(Xva, yva)], eval_metric="average_precision",
            callbacks=[lgb.early_stopping(cfg.early_stopping_rounds, verbose=False)],
        )
    return (lambda X: clf.predict_proba(X)[:, 1]), clf.booster_, {"best_iteration": int(clf.best_iteration_), "num_iteration": int(clf.best_iteration_), "scale_pos_weight": spw}


def build_splits(cfg: TrainConfig):
    sim = Simulator(SimConfig(n_users=cfg.users, seed=cfg.seed, fraud_rate=cfg.fraud_rate))
    txns = sim.advance(cfg.days)
    ds = build_dataset(txns)
    return ds, temporal_split(ds)


def run_training(
    cfg: TrainConfig, model_dir: Path | None = None, reports_dir: Path | None = None, log: Callable[[str], None] = print
) -> dict:
    t0 = time.time()
    ds, (tr, va, te) = build_splits(cfg)
    log(f"dataset: {len(ds):,} transações | treino {len(tr):,} · validação {len(va):,} · teste {len(te):,} | fraude teste {te.y.mean():.2%}")

    candidates: dict[str, dict] = {}
    boosters: dict[str, tuple] = {}
    scorers: dict[str, ScoreFn] = {"baseline_regra_valor": amount_rule_scorer()}
    scorers["logistic_regression"] = fit_logistic_regression(tr.X, tr.y, cfg.seed)
    for family, fit in (("xgboost", _fit_xgboost), ("lightgbm", _fit_lightgbm)):
        for weighted in (False, True):
            name = f"{family}_{'weighted' if weighted else 'plain'}"
            score_fn, booster, info = fit(tr.X, tr.y, va.X, va.y, cfg, weighted)
            scorers[name] = score_fn
            boosters[name] = (family, booster, info)
            log(f"  treinado {name} ({info})")

    for name, fn in scorers.items():
        res = evaluate_candidate(va.y, fn(va.X), te.y, fn(te.X), cfg.max_fpr)
        res["family"] = boosters[name][0] if name in boosters else ("baseline" if name.startswith("baseline") else "linear")
        if name in boosters:
            res["training_info"] = boosters[name][2]
        candidates[name] = res

    selected = max(boosters, key=lambda n: candidates[n]["val_pr_auc"])  # seleção SÓ por validação
    family, booster, info = boosters[selected]
    sel = candidates[selected]
    log(f"selecionado: {selected} (PR-AUC val {sel['val_pr_auc']:.4f}, teste {sel['test_pr_auc']:.4f})")

    result = {
        "config": asdict(cfg),
        "selected": selected,
        "candidates": candidates,
        "split_sizes": {"train": len(tr), "val": len(va), "test": len(te)},
        "test_fraud_rate": float(te.y.mean()),
        "seconds": round(time.time() - t0, 1),
    }

    if model_dir is not None:
        val_scores = scorers[selected](va.X)
        meta = {
            "version": f"{cfg.version_tag}-{selected}-s{cfg.seed}",
            "feature_names": list(FEATURE_NAMES),
            "threshold": sel["threshold"],
            "max_fpr": cfg.max_fpr,
            "threshold_criterion": f"max recall s.t. FPR <= {cfg.max_fpr:.2%} (escolhido na validação)",
            "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "data": {"synthetic": True, "users": cfg.users, "days": cfg.days, "seed": cfg.seed, "fraud_rate": cfg.fraud_rate},
            "split_sizes": result["split_sizes"],
            "metrics": {"val": sel["val"], "test": sel["test"], "val_pr_auc": sel["val_pr_auc"],
                        "test_pr_auc": sel["test_pr_auc"], "test_fraud_rate": sel["test_fraud_rate"]},
            "comparison": {n: {"family": c["family"], "val_pr_auc": c["val_pr_auc"], "test_pr_auc": c["test_pr_auc"],
                               "test": c["test"]} for n, c in candidates.items()},
            "reference": make_reference(va.X, FEATURE_NAMES, val_scores),
        }
        save_artifact(model_dir, booster, family, meta, num_iteration=info["num_iteration"])
        log(f"artefato salvo em {model_dir} ({meta['version']})")

    if reports_dir is not None:
        write_reports(reports_dir, result, te, scorers)
    return result


def write_reports(reports_dir: Path, result: dict, te: FeatureDataset, scorers: dict[str, ScoreFn]) -> None:
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "evaluation.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    (reports_dir / "evaluation.md").write_text(render_markdown(result))
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(6.4, 4.4), dpi=140)
        for name, fn in scorers.items():
            p, r = precision_recall_points(te.y, fn(te.X))
            ax.plot(r, p, lw=2.2 if name == result["selected"] else 1.1, label=f"{name} (PR-AUC {result['candidates'][name]['test_pr_auc']:.3f})")
        ax.axhline(result["test_fraud_rate"], color="grey", ls=":", lw=1, label=f"acaso ({result['test_fraud_rate']:.1%})")
        ax.set_xlabel("Recall"); ax.set_ylabel("Precision"); ax.set_title("Curva Precision-Recall — conjunto de teste")
        ax.set_xlim(0, 1); ax.set_ylim(0, 1.02); ax.legend(fontsize=6.5, loc="lower left"); ax.grid(alpha=0.25)
        fig.tight_layout(); fig.savefig(reports_dir / "pr_curve.png"); plt.close(fig)
    except Exception as exc:  # gráfico é opcional; nunca derruba o treino
        print(f"(aviso) curva PR não gerada: {exc}")


def render_markdown(result: dict) -> str:
    cfg = result["config"]
    lines = [
        f"Dados sintéticos: {cfg['users']} usuários × {cfg['days']} dias, seed {cfg['seed']}, "
        f"split temporal 60/20/20 (teste: {result['split_sizes']['test']:,} transações, {result['test_fraud_rate']:.2%} fraude).",
        f"Threshold por modelo: maior recall com FPR ≤ {cfg['max_fpr']:.0%} na **validação**; teste só reporta.",
        "",
        "| modelo | PR-AUC (teste) | precision | recall | F1 | FPR |",
        "|---|---|---|---|---|---|",
    ]
    for name, c in sorted(result["candidates"].items(), key=lambda kv: -kv[1]["test_pr_auc"]):
        t = c["test"]
        mark = " **(servido)**" if name == result["selected"] else ""
        lines.append(f"| {name}{mark} | {c['test_pr_auc']:.3f} | {t['precision']:.3f} | {t['recall']:.3f} | {t['f1']:.3f} | {t['fpr']:.4f} |")
    return "\n".join(lines) + "\n"


def evaluate_saved_model(model_dir: Path) -> tuple[dict, dict]:
    """Reavalia o artefato salvo no MESMO split (regenerado pela seed do meta.json).
    Retorna (métricas recalculadas, métricas gravadas no artefato)."""
    from app.model.metrics import metrics_at_threshold, pr_auc

    model = FraudModel.load(model_dir)
    d = model.meta["data"]
    cfg = TrainConfig(users=d["users"], days=d["days"], seed=d["seed"], fraud_rate=d["fraud_rate"], max_fpr=model.meta["max_fpr"])
    _, (_, _, te) = build_splits(cfg)
    scores = model.predict(te.X)  # o threshold em vigor é o do artefato
    fresh = {"test_pr_auc": pr_auc(te.y, scores), "test": metrics_at_threshold(te.y, scores, model.threshold)}
    return fresh, model.meta["metrics"]
