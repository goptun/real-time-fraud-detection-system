## Context

Ver `proposal.md` (Why) para a motivação e `specs/` para os requisitos testáveis. Este documento cobre só o *como*.

**Ponto de partida.** Este repositório é greenfield (apenas a estrutura OpenSpec). O plano de origem é a página do Notion "Real-Time Fraud Detection System": pipeline `Transação → Kafka → Feature processing → Modelo → Fraud score → Banco → Dashboard`, stack FastAPI + Kafka + scikit-learn/XGBoost/PyTorch + PostgreSQL + Streamlit/Grafana/Power BI, métricas Precision/Recall/F1/PR-AUC/FPR/monitoramento.

**Padrão a replicar (observado em `rag-knowledge-assistant`, `newsletter-website` e `portfolio-website`, todos locais em `~/Desktop`):**
- Deploy por `git clone` na VPS Oracle (Ubuntu 24.04 ARM64) + Docker Compose; README com fases, resultados reais, trade-offs.
- API FastAPI que serve o próprio frontend estático montado em `/` (`StaticFiles(html=True)`, por último) e usa URLs **relativas** para funcionar atrás de prefixo.
- Nginx do portfólio (`portfolio-website/infra/nginx/matheusramos.dev.conf`, repositório separado) faz proxy de `/projects/rag/` → `127.0.0.1:8000` e `/api/newsletter/` → `127.0.0.1:8001`, removendo o prefixo (barra final em `location` e `proxy_pass`). TLS via Cloudflare Origin Certificate, Full (strict).
- CSP estrita no domínio: `default-src 'self'; script-src 'self' …cloudflareinsights; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self' …` → sem script/estilo inline, sem CDN, sem fontes externas.
- Docker publica portas com `127.0.0.1:` (Docker ignora `ufw`) e o Compose da newsletter usa `name:` explícito porque duas pastas `docker/` viravam o mesmo projeto e `--remove-orphans` apagava os contêineres do RAG. **Este stack deve repetir ambos os cuidados.**
- Portas ocupadas na VPS: 8000 (RAG), 8001 (newsletter), 6333 (Qdrant, publicado sem bind local no compose do RAG), 20128 (9Router, só na tailnet). Este stack usa **8002** e não publica broker/banco no host.

**Restrições.**
- VPS compartilhada; **RAM/CPU livres não medidos** (nenhum documento existente registra). Kafka é o serviço mais pesado do plano; dimensionamento é um gate explícito antes do deploy.
- Repositório `portfolio-website` é separado, com seu próprio OpenSpec: este change **não o edita**; entrega o snippet Nginx e a entrada de projeto prontos (padrão já usado pela newsletter em `infra/nginx/newsletter-location.conf`).
- Dados: nenhum dado real. A demo é pública, então precisa de rate limiting e de retenção limitada.

## Goals / Non-Goals

**Goals:**
- Pipeline ponta a ponta real (broker → features stateful → modelo → banco → dashboard), rodando 24/7 na VPS com tráfego sintético contínuo.
- Avaliação honesta em classe rara (split temporal, threshold escolhido em validação, baseline vs. boosting), para responder "como você sabe que funciona?".
- Paridade treino/serving **por construção**, não por convenção.
- Demo pública segura e barata, dentro de `matheusramos.dev/projects/fraud/`, sem afetar os outros stacks.

**Non-Goals:**
- Dados reais, PCI/LGPD, autenticação de usuários, multi-tenant.
- Retreino automático / MLOps completo (registry, feature store, canary); só monitoramento de drift e artefato versionado.
- Alta disponibilidade (Kafka multi-broker, réplicas de banco); é um broker, uma réplica.
- Persistir transações enviadas por visitantes no pipeline principal (evita poluir métricas e abrir vetor de abuso).
- Grafana/Streamlit/Power BI (ver Decisão 8).
- Edição do repositório `portfolio-website`.

## Decisions

### 1. Dados sintéticos com gerador próprio, não o dataset do Kaggle
O gerador simula perfis por usuário (faixa de valor, dispositivos, cidades, merchants) e injeta fraudes por padrões comportamentais (rajada/velocity, valor fora do histórico, dispositivo novo, viagem impossível), mais outliers legítimos para haver sobreposição de classes. Seed determinística; split temporal.
**Por quê:** o plano lista valor, localização, dispositivo, merchant, timestamp, frequência e histórico — o dataset público mais usado (Credit Card Fraud, Kaggle) tem features anonimizadas por PCA e nenhuma dessas, e não permite *replay* ao vivo com estado por usuário. É o mesmo raciocínio do RAG ("dados sintéticos, pipeline e infra reais"), declarado como limitação no README.
**Alternativas:** Kaggle/IEEE-CIS (features opacas, licença/download, sem replay coerente); só regras fixas de fraude (modelo trivialmente separável, resultado sem valor).
**Risco embutido:** o modelo aprende as regras do gerador. Mitigação: padrões múltiplos, ruído, outliers legítimos e reportar isso como limitação (não como "detecção de fraude real").

### 2. Uma única implementação de features, usada no treino e no streaming
Módulo puro `features` (Python/NumPy, sem pandas no caminho quente): `state + transação → (vetor de features, novo state)`. O treino **reproduz o stream**: percorre as transações em ordem temporal passando-as pela mesma máquina de estados. Não há segunda implementação em pandas/`groupby.rolling`.
**Por quê:** training/serving skew é o bug clássico de ML em produção; paridade por construção + teste de equivalência elimina a categoria. Também evita vazamento do futuro (só o passado entra no state).
**Alternativa:** features em pandas offline e reimplementadas em streaming (rápido de escrever, diverge com o tempo). Custo aceito: treino em loop Python é mais lento (centenas de milhares de eventos → minutos), irrelevante offline.

Features (18): `amount`, `amount_zscore_user`, `amount_ratio_to_user_max`, contagem de transações em 1 min/10 min/1 h/24 h/30 d, `seconds_since_last`, `is_new_device`, `is_new_city/country`, `km_from_last`, `implied_speed_kmh`, `is_new_merchant`, categoria do merchant (código ordinal), `is_risky_category`, `hour_of_day`. Valores padrão documentados para usuário sem histórico.
**Memória rolante de 30 dias (descoberta na implementação).** A 1ª versão usava memória *acumulada* (contagem total, máximo histórico, merchants já vistos). Medido em simulação de 400 dias: PSI de `user_txn_count` > 0,3 já 12 dias após a referência (8,1 no fim) e a taxa de `is_new_merchant` caindo de 14,7% para 4,7% — um replay rodando por semanas geraria alerta falso de drift *e* degradaria o modelo sozinho. Trocou-se por janela de 30 dias (dispositivo/cidade/país/merchant "visto nos últimos 30 dias", `txn_count_30d`, máximo de valor via buckets diários), o que torna as features estacionárias (PSI ≤ 0,025 até o dia 400, coberto por teste de regressão) e limita o estado por usuário — melhor também para produção real. Custo: usuário inativo > 30 dias volta a ser tratado como cold start; a reconstrução do state a partir do banco precisa de todas as transações dos últimos 30 dias (limite de histórico ≥ 500 eventos).

### 3. Modelo: comparar baseline, regressão logística, XGBoost e LightGBM; servir o de maior PR-AUC de validação
Ambos boosting têm wheels `aarch64`; ambos expõem contribuições por feature nativamente (`pred_contribs` / `pred_contrib`), usadas para o "por que foi sinalizado" sem depender de `shap`. Artefato em **formato nativo (JSON/texto), sem pickle**, + `meta.json` (features ordenadas, threshold, versão, métricas, estatísticas de referência). Artefato pequeno é **versionado no git** → o deploy é `git clone`, sem retreinar na VPS (diferente do índice BM25 do RAG, que era regenerado).
**Desbalanceamento:** `scale_pos_weight`/peso de classe comparado com sem peso; a métrica de seleção é PR-AUC (não acurácia, não ROC-AUC). Consequência: scores não são probabilidades calibradas — a decisão é por threshold, e o dashboard chama de "risk score". Calibração isotônica fica como trade-off documentado, não escopo.
**Alternativas:** PyTorch (excesso p/ dados tabulares e imagem pesada em ARM); Isolation Forest/anomaly detection (não usa o rótulo; pode entrar como comparação futura).

### 4. Threshold escolhido em validação com critério explícito
Critério padrão: maximizar Recall sujeito a `FPR ≤ 1%` (configurável), escolhido no split de validação; o teste só faz a avaliação final. O README mostra a curva Precision-Recall e o custo do trade-off (mais recall ↔ mais falsos positivos).
**Por quê:** é a pergunta de entrevista mais provável ("por que esse threshold?"); F1 máximo ignora que falso positivo tem custo operacional distinto de fraude não detectada.

### 5. Kafka (KRaft, nó único) com heap limitado; tópico chaveado por `user_id`
Imagem oficial `apache/kafka` (multi-arch), modo KRaft (sem ZooKeeper), `KAFKA_HEAP_OPTS` ~ 384–512 MB, retenção curta (`retention.ms`/`retention.bytes`). Tópico `transactions` com 3 partições **chaveado por `user_id`** (ordem por usuário garantida → features stateful corretas) e tópico `transactions.rejected` (dead-letter). Cliente `confluent-kafka` (librdkafka; wheels `aarch64`).
**Por quê Kafka e não algo mais leve:** é o que o plano pede e o que o recrutador espera ver. **Gate de recursos:** se a RAM livre medida não comportar o orçamento (Decisão 10), o fallback documentado é Redpanda (API compatível com Kafka, menor footprint) — troca de imagem no Compose, sem mudar código, porque o cliente é o mesmo.
**Alternativas:** Redis Streams/RabbitMQ (mais leves, mas contradizem o plano e "streaming systems" como skill demonstrada); processar direto do produtor sem broker (esconde o ponto do projeto).

### 6. Serviço de scoring: at-least-once + escrita idempotente, estado reconstruível do banco
Loop do consumidor: consome → valida (inválido vai ao dead-letter) → lê o state do usuário → calcula features → pontua → `INSERT … ON CONFLICT (transaction_id) DO NOTHING` → **só se inseriu**, atualiza o state em memória → *commit* do offset. Reentrega gera no máximo um registro e nunca conta duas vezes no state.
**State:** cache em memória por usuário (LRU limitado). Em *cache miss* (boot ou evicção) ele é reconstruído lendo do PostgreSQL as últimas transações do usuário dentro da maior janela usada (24 h). O banco é a fonte de verdade — reiniciar o scorer não perde histórico e **não exige Redis**.
**Alternativas:** Redis para state (mais um serviço/RAM, e o banco já tem os dados); Kafka Streams/Flink (JVM pesada, fora do orçamento e do ecossistema Python); *exactly-once* transacional (complexidade sem ganho perceptível aqui).

### 7. PostgreSQL com retenção, e stream SSE por polling do banco
Tabela `transactions` (PK `transaction_id`, coluna `seq` crescente, features + score + decisão + `model_version` + `event_time`/`scored_at`/latência). Índices por `seq` e `event_time`. Retenção: varredura periódica (ex.: 48 h e teto de linhas) no scorer.
**Relógio (ver Decisão 12):** `event_time` é o timestamp *simulado* do evento; retenção, janelas de estatística/drift e latência usam o relógio *real* (`scored_at`, timestamp do Kafka). Features usam só `event_time`.
**SSE:** uma tarefa de fundo na API consulta `WHERE seq > último` a cada ~1 s e faz *fan-out* para os clientes conectados (custo constante no banco independente do nº de visitantes); *heartbeat* periódico mantém a conexão viva atrás do Nginx/Cloudflare. Cliente `EventSource` reconecta sozinho.
**Alternativas:** `LISTEN/NOTIFY` (mais elegante, mais frágil com pool/reconexão); a API consumir um tópico `scored` (mais um consumidor Kafka na API, acopla a API ao broker). Polling de 1 s é simples, robusto e suficiente para uma demo.

### 8. Dashboard estático próprio, não Streamlit/Grafana/Power BI
HTML/CSS/JS sem build, servido pela própria API (padrão do RAG), reaproveitando os tokens de `rag-knowledge-assistant/app/static/style.css` (já alinhados ao portfólio). Gráficos desenhados em SVG/canvas no próprio JS.
**Por quê:** a CSP do domínio proíbe inline script/style e origens externas; Streamlit (WebSocket + inline) e Grafana (app inteiro sob subpath, iframe bloqueado por `X-Frame-Options: DENY`, `frame-ancestors 'none'`) exigiriam afrouxar a CSP do site principal ou expor outra origem. Power BI é externo e não roda "ao vivo" no site. Streamlit/Grafana continuam citados como alternativa considerada no README.
**Drift:** PSI por feature (e do score) entre a janela recente (ex.: 15 min do banco) e os bins de referência do artefato; cache de ~15 s; alerta acima de 0,2 (limite configurável). O produtor de replay tem um modo de deslocamento (ex.: multiplicar `amount`) usado **para validar** o indicador (documentado no README com resultado real); em produção o padrão é sem drift.

### 9. API: scoring sob demanda *stateless*, rate limit por visitante real
`POST /score` calcula features a partir do `user_id`/histórico (ou de um `context` opcional que sobrescreve as features stateful — usado pelos exemplos prontos legítimo/suspeito), retorna score, decisão, threshold, versão e top-N contribuições; **não persiste** nem altera state. Leitura: `GET` de transações recentes (filtro por sinalizadas), stats (janelas, p50/p95), info do modelo, drift, `/health` (modelo, banco, broker), stream SSE.
**Rate limit:** janela deslizante em memória (padrão do RAG, sem dependência nova), **mas chaveada pelo visitante real**: `CF-Connecting-IP` → primeiro `X-Forwarded-For` → `client.host`, mais um teto global como rede de segurança. **Motivo:** o limiter do RAG usa `request.client.host`, que atrás do Nginx é o IP do gateway Docker — todos os visitantes dividem um único bucket. `CF-Connecting-IP` é forjável por quem acessar a origem sem passar pela Cloudflare; aceito como risco de demo (o teto global limita o dano) e documentado. Aplicar `set_real_ip_from` no Nginx exigiria alterar o bloco principal do server, fora do escopo.
Erros inesperados → 5xx genérico sem *stack trace*; logs sem segredos.

### 10. Compose isolado, orçamento de memória e Nginx por adição
Compose com `name: fraud-detection` (**nunca remover**, pelo mesmo motivo do da newsletter), contêineres `fraud_*`, volumes nomeados, apenas a API publicada em `127.0.0.1:8002`; broker e banco só na rede interna. Serviços: `kafka`, `postgres`, `scorer`, `producer` (replay), `api`, mais um job único de criação de tópicos. Limites `mem_limit` por serviço. Orçamento inicial a validar: Kafka ~768 MB (heap 384–512), Postgres ~256 MB (`shared_buffers` baixo), scorer ~300 MB, API ~300 MB, produtor ~128 MB ⇒ **~1,7 GB**. Medir `free -h`/`docker stats` na VPS antes; se não couber → Decisão 5 (Redpanda) ou reduzir partições/heap.
**Nginx:** um novo `location = /projects/fraud` (301) e `location /projects/fraud/` com `proxy_pass http://127.0.0.1:8002/;`, `proxy_buffering off`, `proxy_cache off`, `Connection ''`, `proxy_read_timeout` alto — snippet autoritativo em `infra/nginx/fraud-location.conf` neste repo (mesclado no repo do portfólio pelo dono), sempre `nginx -t` antes do reload. Só adiciona; não altera blocos existentes.
**Imagem:** dependências de serving leves (sem pandas/sklearn no runtime, sem torch) → build rápido, ao contrário dos ~90 min do RAG; `requirements-train.txt` separado.

### 12. Replay com relógio de simulação acelerado + warm-up
O comportamento por usuário precisa ser o do treino (~4-5 transações/usuário/dia, senão as features de frequência e "novo dispositivo" deslocam), mas uma demo viva precisa de alguns eventos por segundo. O produtor reaproveita o `Simulator` (mesmo código do dataset), avança em dias no tempo *simulado* e publica no ritmo configurado em tempo *real* (ex.: 2/s com 500 usuários ≈ 1 dia simulado a cada ~19 min). Consequências: timestamps de evento são simulados (declarado no dashboard/README); latência e janelas usam relógio real; num reboot o relógio simulado continua depois do último `event_time` persistido (nunca volta no tempo); `id_prefix` por execução evita reemitir ids já persistidos (o INSERT idempotente os descartaria em silêncio).
No primeiro boot (banco vazio) o produtor publica `replay_warmup_days` de histórico sem pausa, para os usuários não começarem todos "frios" (o que distorceria features e o PSI). Limitação aceita: por até uma janela de drift (~15 min) após um banco vazio, o indicador pode refletir o aquecimento.
**Alternativa:** relógio real 1:1 (timestamps "de agora"), exigiria dezenas de milhares de usuários para o mesmo ritmo, com todos frios no início.

### 11. Estrutura do repositório
```
app/
  data/        # gerador sintético + schema
  features/    # máquina de estados de features (fonte única)
  model/       # treino, avaliação, artefato, adapter de serving, drift
  streaming/   # produtor de replay, consumidor/scorer, retenção
  api/         # FastAPI, rate limit, SSE, schemas
  static/      # dashboard (index.html, style.css, app.js)
scripts/       # generate_data, train, evaluate, run_api, run_scorer, run_producer
models/        # artefato versionado (model + meta.json)
docker/        # Dockerfile, docker-compose.yml, .env.example
infra/nginx/   # fraud-location.conf
docs/          # portfolio-entry.md, DEPLOY.md
tests/
```

## Risks / Trade-offs

- **[Kafka pesa demais na VPS compartilhada e derruba outros stacks]** → gate de medição antes do deploy; `mem_limit`; heap limitado; fallback Redpanda; nunca subir sem `docker stats` dos stacks atuais.
- **[Modelo "aprende o gerador" → métricas irrealisticamente boas]** → padrões múltiplos, ruído e outliers legítimos; meta de resultados **plausíveis** (não ~1.0 de PR-AUC); README declara a limitação.
- **[Vazamento temporal / skew treino-serving]** → fonte única de features + teste de paridade + split temporal.
- **[`docker compose down --remove-orphans` apaga stacks vizinhos]** → `name:` explícito, nomes `fraud_*`; documentado no compose e no DEPLOY.
- **[Abuso da demo pública / rate limit contornável]** → limite por visitante + teto global, `/score` stateless, sem persistir dados de visitante, limites de tamanho de payload.
- **[Crescimento ilimitado de disco]** → retenção no banco e no Kafka, produtor com teto de taxa.
- **[Estado em memória perdido em restart]** → reconstrução a partir do PostgreSQL (aumenta a latência só do primeiro evento por usuário).
- **[SSE cortado por proxy/Cloudflare]** → buffering desligado, *heartbeat*, reconexão no cliente; testar de ponta a ponta pelo domínio.
- **[Scores não calibrados]** → rotulados como risk score; threshold por critério de negócio; calibração listada como evolução.
- **[Cloudflare Origin cert / CSP]** → reaproveita o setup existente; frontend validado sob a CSP real (console sem violações).

## Migration Plan

1. Local: gerar dados → treinar → avaliar → commitar o artefato; subir o stack completo via Compose local; testes verdes.
2. VPS (leitura): medir memória/disco/CPU e o consumo atual (`free -h`, `docker stats`); decidir Kafka vs. Redpanda.
3. VPS (deploy): `git clone` deste repo, `.env` a partir de `.env.example`, `docker compose -f docker/docker-compose.yml up -d --build`; verificar `/health` via `127.0.0.1:8002`.
4. Nginx: aplicar o snippet no repo do portfólio, copiar para a VPS, `nginx -t`, reload; verificar `https://matheusramos.dev/projects/fraud/` **e** as rotas vizinhas (`/`, `/projects/rag/`, `/api/newsletter/`).
5. Registrar o projeto no site (entrada em `src/content/projects/`) e redeploy do portfólio pelo processo do `DEPLOY.md` dele.
6. **Rollback:** remover/comentar o `location /projects/fraud/` + `nginx -t` + reload (o site e os outros projetos voltam ao estado anterior); `docker compose -p fraud-detection down` (sem `-v` para preservar dados, ou com `-v` para descartá-los) não toca nos outros projetos.

## Open Questions

- Quanta RAM/CPU livre a VPS tem de fato? (medido na Migração passo 2; só altera Kafka vs. Redpanda e os `mem_limit`, não specs nem tarefas.)
- URL final do repositório no GitHub (para `repoUrl` e o README) — o RAG usa `github.com/goptun/rag-knowledge-assistant`; assume-se a mesma conta.
