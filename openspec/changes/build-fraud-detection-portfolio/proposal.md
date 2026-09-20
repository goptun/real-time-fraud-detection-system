## Why

O portfólio em `matheusramos.dev` já mostra um projeto de IA (RAG Knowledge Assistant), mas nada que cubra o perfil de **ML Engineer**: modelagem em dados desbalanceados, streaming, serviço em produção e monitoramento de modelo. A página "Real-Time Fraud Detection System" no Notion (status "Não iniciada") define exatamente esse projeto; o objetivo agora é construí-lo do mesmo jeito que o RAG — pipeline real, avaliação com métricas, deploy na VPS Oracle e **demo ao vivo dentro do site** — para poder mostrar arquitetura, avaliação e trade-offs numa entrevista (Construir → Medir → Implementar → Explicar).

## What Changes

- Novo projeto greenfield: pipeline de detecção de fraude em tempo real — `Transação → Kafka → Feature processing → Modelo (XGBoost/LightGBM) → Fraud score → PostgreSQL → Dashboard de monitoramento`.
- **Gerador de transações sintéticas** com as features do plano (valor, localização, dispositivo, merchant, timestamp, frequência, histórico do usuário) e padrões de fraude injetados; serve tanto para treino/avaliação offline quanto para alimentar a demo ao vivo.
- **Treino e avaliação do modelo** com comparação baseline vs. modelos de gradient boosting, tratando desbalanceamento, reportando Precision, Recall, F1, PR-AUC e False Positive Rate, e escolhendo o threshold de decisão explicitamente.
- **Serviço de scoring em streaming**: consumidor Kafka que calcula features stateful (frequência/histórico por usuário), pontua com o modelo e persiste transação + score no PostgreSQL.
- **API FastAPI** (`/score`, transações recentes, stats, stream SSE, `/health`) com rate limiting, servindo também o frontend da demo.
- **Dashboard de monitoramento** estático (sem inline script/style, respeitando a CSP do domínio) mostrando o stream ao vivo, taxa de alertas, distribuição de scores, métricas do modelo e sinais de drift; permite ao visitante submeter uma transação manual e ver o score.
- **Deploy em produção** na VPS Oracle via Docker Compose isolado (`name:` explícito, bind em `127.0.0.1`), exposto em `https://matheusramos.dev/projects/fraud/` por um novo bloco `location` no Nginx do portfólio.
- **README recruiter-friendly** (problema, como funciona, arquitetura, stack, resultados, como rodar, trade-offs) e o material para registrar o projeto no site (entrada em `src/content/projects/`, snippet Nginx) — o repositório `portfolio-website` é separado e fica fora do escopo de edição deste change.

## Capabilities

### New Capabilities
- `synthetic-transaction-data`: geração reproduzível de transações rotuladas (fraude/legítima) com as features do plano, usada em treino, avaliação e replay ao vivo.
- `fraud-model-training`: treino, comparação e avaliação offline do modelo (métricas para classe rara, escolha de threshold, artefato versionado e relatório de resultados).
- `streaming-scoring-pipeline`: ingestão via Kafka, cálculo de features stateful, scoring e persistência de transação + score no PostgreSQL.
- `fraud-scoring-api`: API HTTP para scoring sob demanda, consulta de transações/stats, stream SSE, health check e rate limiting.
- `monitoring-dashboard`: frontend de demo ao vivo com stream de transações, métricas operacionais, métricas do modelo e monitoramento de drift.
- `production-deployment`: empacotamento Docker Compose, isolamento na VPS compartilhada, exposição em `/projects/fraud/` via Nginx/Cloudflare e procedimento repetível de deploy/rollback.
- `portfolio-documentation`: README recruiter-friendly com resultados reais e artefatos para integrar o projeto ao site do portfólio.

### Modified Capabilities
<!-- Nenhuma: este repositório ainda não tem specs em openspec/specs/. -->

## Impact

- **Código novo** neste repositório (hoje só contém a estrutura OpenSpec): `app/` (Python/FastAPI), `scripts/`, `tests/`, `docker/`, `infra/nginx/`, `docs/`, `README.md`.
- **Dependências novas**: FastAPI, scikit-learn, XGBoost/LightGBM, Kafka client, SQLAlchemy/psycopg, Faker/NumPy/pandas; serviços Kafka (KRaft) e PostgreSQL no Compose.
- **VPS Oracle compartilhada** (já roda `rag-knowledge-assistant` em `127.0.0.1:8000` e a newsletter em `127.0.0.1:8001`): novo stack isolado em `127.0.0.1:8002`; **consumo de RAM/CPU do Kafka é o principal risco** e precisa ser dimensionado sem derrubar os stacks existentes.
- **Nginx do portfólio** (`portfolio-website/infra/nginx/matheusramos.dev.conf`): um novo `location /projects/fraud/`, sem tocar nos blocos existentes; deploy segue o `DEPLOY.md` do portfólio, com `nginx -t` antes do reload.
- **Site do portfólio**: nova entrada de projeto (`demoUrl` = `https://matheusramos.dev/projects/fraud/`) a ser aplicada no repositório `portfolio-website` pelo dono.
