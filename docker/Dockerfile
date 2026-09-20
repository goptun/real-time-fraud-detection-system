# Imagem de serving (API, scorer e produtor de replay usam a MESMA imagem, comandos diferentes).
# Só dependências de runtime (requirements.txt): sem pandas/scikit-learn/matplotlib — o build é
# rápido também em ARM64 (Oracle Ampere), ao contrário de imagens com torch.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app \
    OMP_NUM_THREADS=1

# libgomp1: runtime OpenMP exigido por XGBoost e LightGBM no Linux.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --home-dir /app app

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt \
    # No Linux x86_64 o xgboost puxa o NCCL (~290 MB, comunicação entre GPUs): inútil em CPU. Em ARM64 nem é instalado.
    && (pip freeze | grep -i '^nvidia-' | cut -d= -f1 | xargs -r pip uninstall -y)

COPY app/ app/
COPY scripts/ scripts/
COPY models/ models/

USER app
EXPOSE 8002
CMD ["uvicorn", "--factory", "app.api.main:create_app", "--host", "0.0.0.0", "--port", "8002"]
