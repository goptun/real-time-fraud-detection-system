## Purpose

Processa transações como um fluxo de eventos: consome do broker, calcula features stateful por usuário, pontua com o modelo e persiste transação e score, de forma resiliente e observável.

## ADDED Requirements

### Requirement: Event ingestion through a broker
As transações SHALL fluir do produtor até o scoring por um tópico do broker de mensagens, desacoplando quem gera a transação de quem a pontua.

#### Scenario: Transaction published and consumed
- **WHEN** um produtor publica uma transação válida no tópico de entrada
- **THEN** o serviço de scoring a consome e a pontua

### Requirement: Stateful per-user features
O pipeline SHALL calcular as features que dependem do histórico do usuário (frequência em janelas de tempo, desvio de valor, dispositivo e localização novos) mantendo estado por usuário, respeitando a ordem dos eventos.

#### Scenario: Velocity feature grows with bursts
- **WHEN** um mesmo usuário gera várias transações em poucos segundos
- **THEN** a feature de frequência de cada transação subsequente reflete o número de transações anteriores na janela

#### Scenario: First transaction of a user
- **WHEN** chega a primeira transação de um usuário sem histórico
- **THEN** o pipeline usa valores padrão documentados para as features de histórico e pontua normalmente

### Requirement: Fraud score and decision
Para cada transação, o pipeline SHALL produzir um score de fraude entre 0 e 1 e uma decisão binária (sinalizada ou não) comparando o score ao threshold do artefato do modelo.

#### Scenario: Score above threshold is flagged
- **WHEN** o score de uma transação é maior ou igual ao threshold
- **THEN** a transação é registrada como sinalizada

### Requirement: Persistence of transactions and scores
Cada transação pontuada SHALL ser persistida no banco de dados com suas features, score, decisão, versão do modelo e timestamps de evento e de processamento.

#### Scenario: Scored transaction stored
- **WHEN** uma transação é pontuada
- **THEN** existe um registro consultável com os campos originais, score, decisão e versão do modelo

### Requirement: Idempotent processing
Reentregas da mesma transação (mesmo identificador) MUST NOT produzir registros duplicados nem contar duas vezes no estado do usuário.

#### Scenario: Duplicate delivery
- **WHEN** a mesma transação é entregue duas vezes ao scoring
- **THEN** o banco contém um único registro para ela

### Requirement: Malformed event handling
Eventos inválidos ou malformados SHALL ser descartados ou desviados para um destino de mensagens rejeitadas, com registro do erro, sem interromper o consumo dos eventos seguintes.

#### Scenario: Invalid payload
- **WHEN** um evento sem campos obrigatórios chega ao tópico
- **THEN** ele é rejeitado com um erro registrado e o próximo evento válido é processado normalmente

### Requirement: Recovery after downtime
Após uma interrupção do serviço de scoring, ele SHALL retomar o consumo de onde parou, sem perder as transações publicadas no intervalo.

#### Scenario: Scoring service restarted
- **WHEN** o serviço de scoring é reiniciado enquanto transações são publicadas
- **THEN** todas as transações publicadas acabam pontuadas e persistidas após a retomada

### Requirement: Replay producer for the live demo
O sistema SHALL incluir um produtor de replay contínuo que publica transações sintéticas em ritmo configurável, incluindo fraudes, para manter a demo ao vivo alimentada, com um teto de taxa e volume de dados retido limitado.

#### Scenario: Continuous synthetic feed
- **WHEN** o produtor de replay está ativo
- **THEN** novas transações sintéticas chegam ao pipeline no ritmo configurado, sem crescimento ilimitado do banco (a retenção descarta registros antigos)

### Requirement: Scoring latency measured
O pipeline SHALL registrar a latência entre o timestamp de evento (publicação) e a persistência do score, para exposição em métricas.

#### Scenario: Latency recorded
- **WHEN** uma transação é pontuada
- **THEN** a latência de ponta a ponta é registrada e agregada para consulta
