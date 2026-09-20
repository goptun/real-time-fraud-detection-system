Dados sintéticos: 2000 usuários × 60 dias, seed 42, split temporal 60/20/20 (teste: 111,563 transações, 1.96% fraude).
Threshold por modelo: maior recall com FPR ≤ 1% na **validação**; teste só reporta.

| modelo | PR-AUC (teste) | precision | recall | F1 | FPR |
|---|---|---|---|---|---|
| xgboost_plain **(servido)** | 0.919 | 0.656 | 0.917 | 0.765 | 0.0096 |
| lightgbm_plain | 0.918 | 0.648 | 0.916 | 0.759 | 0.0100 |
| xgboost_weighted | 0.917 | 0.653 | 0.918 | 0.763 | 0.0098 |
| lightgbm_weighted | 0.805 | 0.648 | 0.881 | 0.747 | 0.0096 |
| logistic_regression | 0.783 | 0.612 | 0.758 | 0.677 | 0.0096 |
| baseline_regra_valor | 0.053 | 0.157 | 0.085 | 0.111 | 0.0092 |
