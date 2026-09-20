## Purpose

Expõe o sistema por HTTP: scoring sob demanda, consulta de transações e estatísticas, stream em tempo real e health check, com proteção contra abuso para poder ficar aberto na internet como demo pública.

## ADDED Requirements

### Requirement: On-demand scoring endpoint
A API SHALL oferecer um endpoint que recebe uma transação (campos do schema, sem rótulo) e retorna o score de fraude, a decisão, o threshold aplicado, a versão do modelo e as features que mais contribuíram para o score.

#### Scenario: Valid transaction scored
- **WHEN** um cliente envia uma transação válida ao endpoint de scoring
- **THEN** a resposta contém score entre 0 e 1, decisão, versão do modelo e as principais features contribuintes

#### Scenario: Invalid transaction rejected
- **WHEN** o cliente envia uma transação com campo obrigatório ausente ou valor inválido
- **THEN** a API responde com erro de validação claro, sem pontuar nem persistir nada

### Requirement: Recent transactions query
A API SHALL permitir listar as transações pontuadas mais recentes, com filtro por sinalizadas e limite de itens, ordenadas da mais recente para a mais antiga.

#### Scenario: Flagged-only filter
- **WHEN** o cliente pede as transações recentes filtrando apenas as sinalizadas
- **THEN** todos os itens retornados têm decisão "sinalizada"

### Requirement: Aggregate statistics
A API SHALL expor estatísticas agregadas em janelas de tempo: volume de transações, taxa de sinalização, distribuição de scores e latência de scoring (p50/p95).

#### Scenario: Stats reflect stored data
- **WHEN** o cliente consulta as estatísticas após transações serem pontuadas
- **THEN** os números refletem as transações persistidas na janela pedida

### Requirement: Live event stream
A API SHALL oferecer um stream de eventos em tempo real com as transações pontuadas conforme chegam, e o stream MUST NOT ser interrompido por buffering de proxy quando servido atrás do Nginx.

#### Scenario: Live transaction appears in the stream
- **WHEN** uma transação é pontuada enquanto um cliente mantém o stream aberto
- **THEN** o cliente recebe o evento da transação em poucos segundos

### Requirement: Model information endpoint
A API SHALL expor a versão do modelo, o threshold, a data de treino e as métricas offline registradas no artefato.

#### Scenario: Model metadata available
- **WHEN** o cliente consulta as informações do modelo
- **THEN** a resposta traz versão, threshold e as métricas do relatório de avaliação

### Requirement: Health check
A API SHALL expor um health check que informa o estado do modelo carregado, do banco e da conexão com o broker, retornando um status de não saudável quando uma dependência crítica falha.

#### Scenario: Dependency down
- **WHEN** o banco de dados está indisponível
- **THEN** o health check reporta a falha da dependência e não um status saudável

### Requirement: Public-demo rate limiting
Endpoints que fazem trabalho por requisição (scoring sob demanda) SHALL ser limitados por visitante, respondendo com erro de limite excedido (HTTP 429) acima do teto configurado.

#### Scenario: Limit exceeded
- **WHEN** um mesmo visitante ultrapassa o limite de requisições de scoring no intervalo
- **THEN** as requisições excedentes recebem HTTP 429 e as demais continuam sendo atendidas normalmente

### Requirement: Sub-path compatibility
A API e o frontend servido por ela SHALL funcionar quando montados atrás de um prefixo de caminho removido pelo proxy (por exemplo, `/projects/fraud/`), usando apenas URLs relativas.

#### Scenario: Served behind a prefix
- **WHEN** o frontend é acessado via `/projects/fraud/` com o proxy removendo o prefixo
- **THEN** todas as chamadas de API e carregamento de assets funcionam sem configuração específica de caminho na API

### Requirement: No sensitive data exposure
Respostas e logs MUST NOT expor segredos, credenciais ou detalhes internos de infraestrutura, e erros inesperados SHALL retornar uma mensagem genérica sem stack trace.

#### Scenario: Unexpected server error
- **WHEN** ocorre um erro interno inesperado durante uma requisição
- **THEN** o cliente recebe uma resposta 5xx genérica, sem stack trace nem dados de configuração
