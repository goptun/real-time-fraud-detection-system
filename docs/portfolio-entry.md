# Entrada do projeto no site (matheusramos.dev)

O site é o repositório `portfolio-website` (Astro), separado deste. Para listar o projeto, crie
`portfolio-website/src/content/projects/real-time-fraud-detection.md` com o conteúdo abaixo — o frontmatter segue o schema de
`src/content.config.ts` (`title`, `description`, `tags`, `repoUrl`, `demoUrl`, `date`), igual ao de `rag-knowledge-assistant.md`:

```markdown
---
title: "Real-Time Fraud Detection System"
description: "Pipeline de detecção de fraude em tempo real: transações via Kafka, features por usuário em streaming (memória rolante de 30 dias), modelo XGBoost (PR-AUC 0,919; recall 91,7% com FPR ≤ 1% em split temporal), persistência idempotente em PostgreSQL, monitoramento de drift por PSI e dashboard ao vivo (SSE). Dados sintéticos; deploy em produção via Docker Compose numa VPS Oracle Cloud."
tags: ["Python", "Kafka", "XGBoost", "FastAPI", "PostgreSQL", "MLOps", "Docker"]
repoUrl: "https://github.com/goptun/real-time-fraud-detection-system"
demoUrl: "https://matheusramos.dev/projects/fraud/"
date: 2026-09-20
---
```

Depois: `npm run build` e o `./scripts/deploy.sh` do portfólio (ver o `DEPLOY.md` dele). O card deve aparecer em `/projects/` com o
link da demo. **Só faça isso depois** de a demo estar no ar (`docs/DEPLOY.md`), para o link não apontar para uma página inexistente.

O bloco do Nginx da demo está em `infra/nginx/fraud-location.conf` (instruções em `docs/DEPLOY.md`, seção 3).
