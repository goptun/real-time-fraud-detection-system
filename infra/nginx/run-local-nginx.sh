#!/usr/bin/env bash
# Sobe um Nginx local (porta 8080) com a CSP de produção + o snippet fraud-location.conf,
# apontando para a API rodando no host (porta 8002). Para: docker rm -f fraud_nginx_test
set -euo pipefail
cd "$(dirname "$0")"
TMP="$(mktemp -d)"
# no teste o upstream é o host (a API roda fora do Docker); em produção é 127.0.0.1:8002
sed 's#http://127.0.0.1:8002/#http://host.docker.internal:8002/#' fraud-location.conf > "$TMP/fraud-location.conf"
cp test-server.conf.template "$TMP/default.conf"
docker rm -f fraud_nginx_test >/dev/null 2>&1 || true
docker run -d --name fraud_nginx_test -p 127.0.0.1:8080:8080 \
  -v "$TMP/default.conf:/etc/nginx/conf.d/default.conf:ro" \
  -v "$TMP/fraud-location.conf:/etc/nginx/snippets/fraud-location.conf:ro" \
  nginx:1.27-alpine >/dev/null
docker exec fraud_nginx_test nginx -t
echo "Nginx de teste em http://127.0.0.1:8080/projects/fraud/ (API esperada em :8002 no host)"
