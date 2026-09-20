# Deploy

Stack em Docker Compose numa VPS Oracle Cloud (Ubuntu 24.04, ARM64) **compartilhada** com outros projetos
(`rag-knowledge-assistant` em `127.0.0.1:8000`, newsletter em `127.0.0.1:8001`, e o site em `matheusramos.dev`).
Tudo aqui foi pensado para **não afetar os vizinhos**: projeto Compose com `name:` explícito, contêineres `fraud_*`,
volumes próprios, só a API publicada e só em `127.0.0.1:8002`, e o Nginx alterado apenas por adição.

Host, chave SSH e o fluxo de Nginx/Cloudflare do site principal estão no `DEPLOY.md` do repositório `portfolio-website`
(`export DEPLOY_HOST=ubuntu@<ip>` e `export DEPLOY_KEY=<caminho da chave>`).

## 0. Regras que nunca se quebram

- **Nunca** `docker compose down --remove-orphans` a partir de uma pasta `docker/` sem `-p fraud-detection` / sem o `name:` do arquivo: outros stacks da VPS também têm uma pasta `docker/`.
- **Nunca** publicar broker/banco no host, nem a API em `0.0.0.0` (o Docker ignora o `ufw`).
- **Sempre** `nginx -t` antes do `reload`: um erro derruba todos os sites da VPS.
- Não commitar o `.env`. Use senha **URL-safe** em `POSTGRES_PASSWORD` (ela entra numa URL): `openssl rand -hex 16`.

## 1. Gate de recursos (somente leitura — faça ANTES de subir)

```bash
ssh -i "$DEPLOY_KEY" "$DEPLOY_HOST" '
  uname -m; nproc
  free -h
  df -h /
  docker ps --format "table {{.Names}}\t{{.Status}}"
  docker stats --no-stream --format "table {{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}"
'
```

Medido localmente (imagem construída, stack completo): **~735 MiB em regime, ~913 MiB de pico** no aquecimento (Kafka ~430–510,
scorer ~80, produtor ~65–165, API ~85, Postgres ~70) e imagem de 705 MB. Só siga se a memória **disponível**
(`available` do `free -h`, já contando os stacks atuais) for ≥ ~1,5 GiB e houver ≥ 3 GB de disco.
Se não couber, em ordem: (1) reduzir `REPLAY_USERS` (menos estado/aquecimento), (2) trocar o Kafka por Redpanda no
Compose (mesma API Kafka, menor footprint; nenhum código muda), (3) reduzir o heap em `KAFKA_HEAP_OPTS`.
**Medido na VPS em 2026-09-20 (somente leitura):** `aarch64` · **2 núcleos** (load 0,00) · RAM 11 GiB total, **9,7 GiB disponíveis** (1,9 GiB em uso pelos stacks atuais; sem swap) · disco 172 GB livres (11% usado) · portas 8002/9092/5432 livres · vizinhos: `rag_api` 1,1 GiB, `rag_qdrant`, `rag_router`, `newsletter_api`. Memória sobra; o recurso escasso é **CPU (2 núcleos)**. Por isso o Compose dá aos serviços `cpu_shares` baixo (os vizinhos ganham sob contenção), tetos de CPU (`cpus`) nos mais gulosos e um aquecimento mais lento (`REPLAY_WARMUP_RATE_PER_SEC=120`, ~11 min): validado localmente com esses limites, o aquecimento usa ~0,75 núcleo somados (scorer ~30%, Kafka 20–45%) e o regime estável, ~1–6%.

## 2. Subir o stack

```bash
ssh -i "$DEPLOY_KEY" "$DEPLOY_HOST"
git clone https://github.com/goptun/real-time-fraud-detection-system.git && cd real-time-fraud-detection-system
cp .env.example .env && nano .env            # POSTGRES_PASSWORD forte; demais valores padrão servem
sudo docker compose -f docker/docker-compose.yml --env-file .env up -d --build
```

O artefato do modelo (`models/`) vem no `git clone` — não há treino na VPS. O primeiro boot publica 35 dias de histórico
simulado (~5 min) e depois segue em tempo real; enquanto isso o painel mostra um pico de tráfego e o drift pode oscilar.

Verificação local (na VPS):

```bash
sudo docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'   # só fraud_api publica porta (127.0.0.1:8002)
curl -s http://127.0.0.1:8002/health                                    # {"status":"ok",...}
sudo docker stats --no-stream                                           # confira a memória
sudo docker logs fraud_producer 2>&1 | grep warmup                      # "warmup concluído"
```

E confirme que os vizinhos seguem de pé: `sudo docker ps` (RAG, newsletter) e `curl -sI https://matheusramos.dev/projects/rag/`.

## 3. Nginx (repositório `portfolio-website`)

1. Mescle `infra/nginx/fraud-location.conf` (deste repo) dentro do `server { listen 443 ssl; server_name matheusramos.dev; ... }`
   de `portfolio-website/infra/nginx/matheusramos.dev.conf`, ao lado do bloco `/projects/rag/`. É só **adição**.
2. Aplique como no `DEPLOY.md` do portfólio (`scp` → `mv` para `/etc/nginx/sites-available/matheusramos.dev` → **`sudo nginx -t`** → `sudo systemctl reload nginx`).
3. Teste o mesmo bloco antes, sem tocar em produção: `./infra/nginx/run-local-nginx.sh` sobe um Nginx local com a CSP real do domínio.

## 4. Verificação pós-deploy

```bash
curl -sI  https://matheusramos.dev/projects/fraud            # 301 -> /projects/fraud/
curl -s   https://matheusramos.dev/projects/fraud/health     # status ok
curl -sN --max-time 5 https://matheusramos.dev/projects/fraud/stream | head   # eventos SSE chegando
curl -s -X POST https://matheusramos.dev/projects/fraud/score -H 'Content-Type: application/json' \
  -d '{"amount":59.9,"city":"São Paulo","merchant_category":"groceries"}'      # score + top_features
# vizinhos intactos:
curl -sI https://matheusramos.dev/ ; curl -sI https://matheusramos.dev/projects/rag/ ; curl -sI https://matheusramos.dev/api/newsletter/
# broker/banco/API não respondem no IP público:
nc -zv <ip-publico> 9092 5432 8002        # todas devem recusar
```

Abra `https://matheusramos.dev/projects/fraud/` num navegador e confira o console: **sem violações de CSP**.

## 5. Atualizar

```bash
cd real-time-fraud-detection-system && git pull
sudo docker compose -f docker/docker-compose.yml --env-file .env up -d --build   # recria só o que mudou
```

Para publicar um modelo novo: `python3 scripts/train.py` localmente, revisar `reports/`, commitar `models/` e `reports/`, e
fazer o passo acima. **Mudou o conjunto de features? Retreine** (o scorer recusa subir se as features do artefato não baterem).

## 6. Rollback

- **Só a demo quebrou o site/Nginx:** remova (ou comente) os dois blocos `location /projects/fraud` no Nginx, `sudo nginx -t` e `reload`.
  O resto do domínio volta ao estado anterior instantaneamente.
- **Versão anterior do código:** `git checkout <commit-anterior>` e `up -d --build`.
- **Remover o stack sem tocar nos vizinhos:** `sudo docker compose -p fraud-detection -f docker/docker-compose.yml --env-file .env down`
  (acrescente `-v` para descartar também os dados; **nunca** `--remove-orphans`).

## 7. Operação

- **Zerar a demo** (histórico e aquecimento de novo): `down`, `docker volume rm fraud-detection_postgres_data fraud-detection_kafka_data`, `up -d`.
- **Mostrar o drift ao vivo:** no `.env`, `REPLAY_DRIFT_FEATURE=amount` e `REPLAY_DRIFT_FACTOR=4`, depois `up -d producer`; o indicador
  sobe em alguns minutos. Volte para vazio/`1.0` depois.
- **Disco/retenção:** Kafka retém 1 h (segmentos de 10 min); o banco mantém 48 h e no máximo 200 mil linhas (varredura a cada 5 min).
- **Logs:** `sudo docker logs -f fraud_scorer` (contadores a cada 30 s), `fraud_producer`, `fraud_api`.

## 8. Observação pós-deploy (tarefa 9.7)

**Deploy realizado em 2026-09-20.** Nginx: config anterior salva em `~/nginx-backup/matheusramos.dev.20260920-172634` na VPS
(rollback: `sudo cp -p` de volta para `/etc/nginx/sites-available/matheusramos.dev`, `sudo nginx -t`, `sudo systemctl reload nginx`).

Linha de base ~30 min após o deploy: 79 mil linhas (46 MB de banco) · Kafka 33 MB **reais** em disco · memória do stack ~720 MiB, 9,0 GiB disponíveis
na VPS · 0 reinícios / 0 OOM · latência ao vivo p50 22 ms / p95 23 ms · drift `ok` (maior PSI 0,059).

> Atenção ao medir o Kafka: `docker system df -v` mostra ~1,2 GB para `fraud-detection_kafka_data`, mas é o **tamanho aparente**
> dos índices esparsos pré-alocados (10 KB × 3 arquivos × 50 partições do `__consumer_offsets`). Use o uso real:
> `sudo du -sh /var/lib/docker/volumes/fraud-detection_kafka_data/_data` (33 MB).

Para conferir a estabilidade e a retenção depois de algumas horas/dias (o teto de 200 mil linhas é atingido em ~14 h; a janela de 48 h, em 2 dias):

```bash
sudo docker exec fraud_postgres psql -U fraud -d fraud -tAc \
  "select count(*), min(scored_at), pg_size_pretty(pg_database_size(current_database())) from transactions"   # <= ~200 mil linhas, min(scored_at) <= 48 h
sudo du -sh /var/lib/docker/volumes/fraud-detection_kafka_data/_data                                          # deve ficar em dezenas de MB
sudo docker inspect -f '{{.Name}} restarts={{.RestartCount}} oom={{.State.OOMKilled}}' $(sudo docker ps -q --filter name=fraud_)
sudo docker stats --no-stream --format '{{.Name}} {{.MemUsage}}' | grep fraud_ ; free -h | sed -n 2p ; df -h / | tail -1
curl -s http://127.0.0.1:8002/drift | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d["status"], d["alerts"])'
```

