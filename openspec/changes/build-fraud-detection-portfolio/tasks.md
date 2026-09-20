## 1. Project setup

- [x] 1.1 Inicializar o repositório git local, `.gitignore` (`.env`, `.venv`, `__pycache__`, `data/*.parquet|csv` gerados, `.DS_Store`) e a estrutura de pastas do design (Decisão 11); verificar com `ls`/`git status` que `.env` é ignorado
- [x] 1.2 Criar `requirements.txt` (serving: FastAPI, uvicorn, confluent-kafka, psycopg[pool], numpy, xgboost, lightgbm, pydantic-settings) e `requirements-train.txt` (scikit-learn, pandas, matplotlib); verificar `pip install` em venv Python 3.12 limpo
- [x] 1.3 Criar `app/config` com settings por variáveis de ambiente e `.env.example` só com valores fictícios; verificar que o app importa sem `.env` real
- [x] 1.4 Configurar execução de testes (`python3 -m unittest discover -s tests`, padrão do RAG) com um teste "smoke"; verificar que roda verde

## 2. Synthetic transaction data (spec: synthetic-transaction-data)

- [x] 2.1 Definir o schema de transação (campos, tipos, validação) em `app/data/schema.py`; verificar com testes de transação válida e de campo ausente/valor não positivo
- [x] 2.2 Implementar perfis de usuário e a tabela de cidades/merchants sintéticos; verificar por teste que transações legítimas respeitam o perfil e que existem outliers legítimos (valor alto, viagem)
- [x] 2.3 Implementar os padrões de fraude (velocity, valor fora do histórico, dispositivo novo, viagem impossível) com taxa alvo configurável (<5%); verificar por teste que cada padrão aparece e que nenhum explica 100% das fraudes
- [x] 2.4 Garantir seed determinística e ordenação temporal; verificar por teste que duas execuções com a mesma seed são idênticas e que o split temporal não tem sobreposição
- [x] 2.5 Criar `scripts/generate_data.py` (usuários, dias, seed, taxa de fraude) e gerar o dataset de treino; verificar o resumo impresso (total, taxa de fraude, distribuição por padrão)

## 3. Feature state machine (spec: fraud-model-training — training/serving parity)

- [x] 3.1 Implementar `app/features` como máquina de estados pura (`state + transação → features + novo state`) com valores padrão para usuário sem histórico; verificar com testes unitários por feature (janelas 1 min/10 min/1 h/24 h, novo dispositivo/cidade/merchant, `km_from_last`, `implied_speed_kmh`, z-score do valor)
- [x] 3.2 Garantir ausência de vazamento do futuro e tratamento de eventos fora de ordem/duplicados; verificar por teste que só eventos anteriores contribuem e que reprocessar o mesmo `transaction_id` não altera o state
- [x] 3.3 Implementar o construtor offline de dataset que reproduz o stream pela mesma máquina de estados; verificar por teste de paridade que a sequência offline e a "streaming" geram vetores idênticos (tolerância numérica definida)
- [x] 3.4 Implementar o cache de state por usuário com LRU e reconstrução a partir de uma lista de transações recentes (interface usada depois pelo PostgreSQL); verificar por teste que o state reconstruído produz as mesmas features que o state contínuo

## 4. Model training and evaluation (spec: fraud-model-training)

- [x] 4.1 Implementar split temporal treino/validação/teste (ex.: 60/20/20 por tempo); verificar por teste que todo treino é anterior à validação, que é anterior ao teste
- [x] 4.2 Implementar baseline (regra por limiar) e regressão logística; verificar que geram scores e métricas no mesmo conjunto de teste
- [x] 4.3 Implementar treino de XGBoost e LightGBM com e sem peso de classe; verificar que o treino roda, é reprodutível por seed e registra PR-AUC de validação de cada variante
- [x] 4.4 Implementar métricas (Precision, Recall, F1, PR-AUC, FPR, taxa de fraude do teste) e a seleção de threshold (Recall máximo com FPR ≤ alvo, só em validação); verificar por teste com dados sintéticos pequenos e que o teste não é usado na escolha
- [x] 4.5 Implementar geração do artefato (modelo em formato nativo sem pickle + `meta.json` com features ordenadas, threshold, versão, métricas, data) e as estatísticas de referência por feature e de scores (bins p/ PSI); verificar que o artefato recarrega e reproduz os mesmos scores
- [x] 4.6 Criar `scripts/train.py` e `scripts/evaluate.py` que executam o processo completo e geram o relatório comparativo (tabela baseline vs. LR vs. XGBoost vs. LightGBM, curva PR); executar de verdade e verificar que as métricas são plausíveis (sem PR-AUC ≈ 1.0 — se estiver, revisar o gerador)
- [x] 4.7 Versionar o artefato escolhido em `models/` e registrar os resultados reais (para o README); verificar que `scripts/evaluate.py` reproduz os números dentro da tolerância documentada
- [x] 4.8 Implementar o adapter de serving (`predict`, `explain` com top-N contribuições, suporte a XGBoost e LightGBM) e testar contra o artefato; verificar que as contribuições somam de forma consistente e que a versão/threshold são expostos

## 5. Streaming pipeline (spec: streaming-scoring-pipeline)

- [x] 5.1 Criar o `docker-compose` de desenvolvimento com Kafka (KRaft) e PostgreSQL e o job de criação de tópicos (`transactions` com 3 partições chaveado por `user_id`, `transactions.rejected`, retenção curta); verificar com `docker compose up` e listagem de tópicos
- [x] 5.2 Implementar o schema SQL e a camada de acesso (`transactions` com `seq`, PK `transaction_id`, índices, insert idempotente, consulta de histórico por usuário, retenção); verificar com testes contra um PostgreSQL de teste
- [x] 5.3 Implementar o scorer: consumo → validação (inválidos ao dead-letter) → features → score → insert idempotente → atualização do state só se inseriu → commit do offset; verificar com teste de integração publicando transações válidas, duplicadas e malformadas
- [x] 5.4 Registrar latência de ponta a ponta (evento → persistido) e a versão do modelo por registro; verificar que os campos são gravados e agregáveis
- [x] 5.5 Implementar a varredura de retenção do banco (janela + teto de linhas) e a configuração de retenção do Kafka; verificar por teste que registros antigos são removidos e os recentes ficam
- [x] 5.6 Implementar o produtor de replay (ritmo configurável com teto, fraudes incluídas, modo opcional de drift em uma feature); verificar que o ritmo é respeitado e que o modo de drift desloca a distribuição
- [x] 5.7 Verificar a recuperação: reiniciar o scorer com o produtor ativo e confirmar que nenhuma transação publicada é perdida e que não há duplicatas (contagem publicada × persistida)

## 6. Scoring API (spec: fraud-scoring-api)

- [x] 6.1 Criar o app FastAPI com carregamento do artefato no startup, pool de banco, `/health` (modelo, banco, broker; não saudável se dependência crítica cai) e tratamento de erro genérico 5xx; verificar com testes de API, incluindo banco indisponível
- [x] 6.2 Implementar `POST /score` stateless (validação, `context` opcional que sobrescreve features stateful, retorno de score/decisão/threshold/versão/top-N contribuições, limite de tamanho de payload); verificar com testes de transação válida, inválida e com `context`
- [x] 6.3 Implementar consultas de transações recentes (filtro por sinalizadas, limite), estatísticas em janelas (volume, taxa de sinalização, distribuição de scores, p50/p95) e info do modelo; verificar com testes contra dados persistidos
- [x] 6.4 Implementar o cálculo de drift (PSI por feature e do score vs. referência do artefato, janela recente, cache ~15 s, limite configurável) e o endpoint; verificar por teste que dados na distribuição de referência ficam abaixo do limite e dados deslocados o ultrapassam
- [x] 6.5 Implementar o stream SSE (tarefa de fundo com polling por `seq`, fan-out, heartbeat, encerramento limpo dos clientes); verificar por teste que uma transação nova chega ao cliente conectado e que a desconexão é tratada
- [x] 6.6 Implementar o rate limiter por visitante real (`CF-Connecting-IP` → `X-Forwarded-For` → `client.host`) com teto global, aplicado ao `/score`; verificar por teste que visitantes distintos têm buckets distintos, que o excedente recebe 429 e que o teto global funciona
- [x] 6.7 Montar o frontend estático em `/` por último (padrão do RAG) e validar o funcionamento atrás de prefixo (URLs relativas) com um teste que simula o proxy removendo `/projects/fraud/`; verificar `curl` local e por teste

## 7. Monitoring dashboard (spec: monitoring-dashboard)

- [x] 7.1 Criar `index.html`/`style.css`/`app.js` sem inline script/style, com os tokens visuais do portfólio (reaproveitar `rag-knowledge-assistant/app/static/style.css`), aviso de dados sintéticos e links (repo, site); verificar no navegador que o layout carrega e que não há violações de CSP com os headers do domínio
- [x] 7.2 Implementar o feed ao vivo via `EventSource` com destaque das sinalizadas e limite de itens na tela; verificar no navegador com o stack local rodando (novas transações sem recarregar)
- [x] 7.3 Implementar o painel operacional (transações/min, taxa de sinalização, p50/p95, distribuição de scores) com gráficos próprios em SVG/canvas; consultar a skill `dataviz` antes; verificar visualmente em tema claro e escuro
- [x] 7.4 Implementar o painel de qualidade do modelo (métricas offline, threshold, versão, baseline vs. modelo) e o indicador de drift com alerta; verificar com o produtor em modo normal (abaixo do limite) e em modo de drift (alerta)
- [x] 7.5 Implementar o "try it yourself" (formulário, exemplos legítimo/suspeito, score + top features, mensagem clara de 429); verificar no navegador que o exemplo suspeito retorna score alto
- [x] 7.6 Implementar o estado de desconexão com reconexão automática e ausência de dados obsoletos apresentados como atuais; verificar derrubando a API e subindo de novo
- [x] 7.7 Verificar responsividade em largura de celular (sem rolagem horizontal) e acessibilidade básica (contraste, foco, `prefers-reduced-motion`) com capturas de tela em claro/escuro/mobile

## 8. Containerization and resource budget (spec: production-deployment)

- [x] 8.1 Escrever `docker/Dockerfile` (imagem de serving leve, sem pandas/sklearn) e `docker/docker-compose.yml` de produção com `name: fraud-detection` (comentário "nunca remover"), contêineres `fraud_*`, volumes nomeados, apenas a API em `127.0.0.1:8002`, broker e banco só na rede interna, `restart: unless-stopped`, `mem_limit` por serviço e healthchecks; verificar `docker compose config` e o build local
- [x] 8.2 Subir o stack de produção localmente ponta a ponta e medir consumo com `docker stats` sob o replay contínuo por tempo suficiente; verificar que a memória fica estável dentro dos limites e que o total confere com o orçamento do design
- [x] 8.3 Confirmar por teste local que apenas `127.0.0.1:8002` é publicado (`docker compose ps`/`ss`) e que broker e banco não estão acessíveis do host pela rede externa

## 9. Deploy to the VPS (spec: production-deployment)

- [x] 9.1 **Gate de recursos:** na VPS, medir `free -h`, disco, CPU e `docker stats` dos stacks existentes (somente leitura) e confirmar que o orçamento deste stack cabe com margem; se não couber, aplicar o fallback do design (Redpanda ou heap/partições menores) e atualizar o compose antes de seguir. Registrar os números no `docs/DEPLOY.md`
- [ ] 9.2 Publicar o repositório no GitHub (conta `goptun`, como o RAG), sem segredos versionados; verificar varredura de segredos no histórico e `.env` no gitignore
- [ ] 9.3 Na VPS: `git clone`, criar `.env`, `docker compose -f docker/docker-compose.yml --env-file .env up -d --build` (sem `--remove-orphans`); verificar `curl http://127.0.0.1:8002/health` saudável e que os contêineres do RAG e da newsletter continuam de pé (`docker ps`)
- [x] 9.4 Escrever `infra/nginx/fraud-location.conf` (301 de `/projects/fraud` + proxy com prefixo removido, buffering off, timeout alto); verificar localmente com `nginx -t` (ou por revisão contra o bloco do RAG) que a sintaxe é válida
- [ ] 9.5 Aplicar o bloco no Nginx do portfólio seguindo o `DEPLOY.md` dele (repositório `portfolio-website`, mesclando o snippet, `scp`, `nginx -t`, reload — **peça confirmação ao dono antes de alterar a config de produção**); verificar `https://matheusramos.dev/projects/fraud/`, o stream ao vivo e o `/score` de exemplo pelo domínio
- [ ] 9.6 Verificar que as rotas vizinhas seguem intactas (`/`, `/projects/rag/`, `/api/newsletter/`) e que broker/banco/8002 não respondem pelo IP público; verificar o dashboard no navegador sob a CSP real (console sem violações)
- [ ] 9.7 Observar o stack por um período (memória, reinícios, crescimento de disco/retention) e registrar; verificar que não há reinícios por OOM e que os outros stacks seguem estáveis

## 10. Documentation and portfolio integration (spec: portfolio-documentation)

- [x] 10.1 Escrever `README.md` recruiter-friendly: problema, como funciona, arquitetura, stack, resultados reais, como rodar, link da demo, aviso de dados sintéticos e limitações; verificar que todo comando documentado foi executado com sucesso
- [x] 10.2 Adicionar ao README os diagramas Mermaid (pipeline e deploy Cloudflare → Nginx → stack) refletindo o que foi realmente implantado; verificar a renderização
- [x] 10.3 Adicionar as tabelas de resultados (baseline vs. modelos, métricas no threshold operacional, curva PR, latência de scoring, experimento de drift) com a metodologia (dados sintéticos, split temporal, seed) e o comando que reproduz cada número
- [x] 10.4 Escrever a seção de trade-offs (por que Kafka e o dimensionamento, por que dashboard próprio vs. Grafana/Streamlit, threshold, scores não calibrados, dados sintéticos, rate limit por visitante) e `docs/DEPLOY.md` (deploy, verificação, rollback, cuidados de isolamento na VPS compartilhada)
- [x] 10.5 Criar `docs/portfolio-entry.md` com o frontmatter pronto para `portfolio-website/src/content/projects/` (título, descrição, tags, `repoUrl`, `demoUrl` = `https://matheusramos.dev/projects/fraud/`, data) e as instruções de aplicação; verificar que o frontmatter valida contra o schema em `portfolio-website/src/content.config.ts`
- [ ] 10.6 Registrar o projeto no site (entrada + redeploy do portfólio pelo processo do `DEPLOY.md` dele) **somente com a confirmação do dono**; verificar que o card aparece em `/projects` com o link da demo
- [ ] 10.7 Atualizar a página do Notion "Real-Time Fraud Detection System" (status → Concluído, link da demo/repo) **somente com a confirmação do dono**; verificar a propriedade Status na base "Lista"
