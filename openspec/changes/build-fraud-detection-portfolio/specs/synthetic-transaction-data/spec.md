## Purpose

Fornece transações sintéticas rotuladas (legítimas e fraudulentas) com as features do plano do projeto, de forma reproduzível, para treinar e avaliar o modelo e para alimentar a demo ao vivo sem expor nenhum dado real.

## ADDED Requirements

### Requirement: Transaction schema
Cada transação gerada SHALL conter: identificador único, identificador do usuário, valor, moeda, localização (país e cidade), identificador de dispositivo, merchant (identificador e categoria), timestamp e o rótulo de verdade-terreno (`is_fraud`).

#### Scenario: Generated transaction is complete
- **WHEN** o gerador produz uma transação
- **THEN** todos os campos do schema estão presentes, com tipos válidos e valor positivo

### Requirement: Reproducible generation
O gerador SHALL aceitar uma seed e produzir exatamente o mesmo conjunto de transações para a mesma seed e a mesma configuração.

#### Scenario: Same seed yields same dataset
- **WHEN** o gerador roda duas vezes com a mesma seed e configuração
- **THEN** os dois conjuntos são idênticos, incluindo os rótulos

### Requirement: Rare and configurable fraud rate
A proporção de fraudes SHALL ser configurável e o padrão SHALL ser uma classe rara (inferior a 5% das transações), de modo que o desbalanceamento seja real.

#### Scenario: Default fraud rate
- **WHEN** um dataset é gerado com a configuração padrão
- **THEN** a fração de transações fraudulentas fica dentro da tolerância configurada em torno do alvo, abaixo de 5%

### Requirement: Behavioral fraud patterns
As fraudes SHALL ser geradas por padrões comportamentais distintos, e não por um único limiar de valor, incluindo ao menos: rajada de transações em curto intervalo (velocity), valor muito acima do histórico do usuário, uso de dispositivo nunca visto para o usuário, e localização incompatível com a anterior no intervalo de tempo.

#### Scenario: Multiple fraud patterns present
- **WHEN** um dataset padrão é gerado
- **THEN** cada padrão de fraude listado aparece em pelo menos uma fração mínima das fraudes, e nenhum padrão isolado explica todas elas

### Requirement: Realistic legitimate behavior and overlap
Transações legítimas SHALL seguir perfis por usuário (faixa de valor, dispositivos, cidades e merchants habituais) e SHALL incluir casos atípicos legítimos (por exemplo, compra de valor alto ou viagem), de modo que as classes se sobreponham e o problema não seja trivialmente separável.

#### Scenario: Legitimate outliers exist
- **WHEN** um dataset padrão é gerado
- **THEN** existem transações legítimas com valor alto ou dispositivo/localização novos, rotuladas como não fraude

### Requirement: Temporal ordering and time-based splits
O dataset SHALL ser ordenado por timestamp e o processo de avaliação SHALL poder dividi-lo por tempo (treino no passado, teste no futuro) sem vazamento de informação futura.

#### Scenario: Chronological split
- **WHEN** o dataset é dividido em treino e teste por um ponto no tempo
- **THEN** todas as transações de treino são anteriores às de teste

### Requirement: No real personal data
O gerador MUST NOT usar nem exigir dados pessoais ou financeiros reais; todos os identificadores, nomes e localizações SHALL ser sintéticos.

#### Scenario: Data is clearly synthetic
- **WHEN** um usuário inspeciona os dados gerados ou a documentação
- **THEN** não há dado real e a documentação declara que os dados são sintéticos
