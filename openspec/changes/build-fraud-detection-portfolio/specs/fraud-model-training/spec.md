## Purpose

Treina e avalia o modelo de fraude offline com métricas adequadas a classes raras, compara alternativas de forma honesta e produz um artefato versionado e um relatório de resultados reprodutíveis.

## ADDED Requirements

### Requirement: Feature engineering shared with serving
As features derivadas (por exemplo, frequência de transações por janela de tempo, desvio do valor em relação ao histórico do usuário, dispositivo/localização novos) SHALL ser calculadas pela mesma lógica no treino e no scoring em streaming, e MUST NOT usar informação posterior ao timestamp da transação.

#### Scenario: Training/serving parity
- **WHEN** a mesma sequência de transações é processada pelo cálculo offline e pelo cálculo em streaming
- **THEN** os vetores de features resultantes são equivalentes dentro de uma tolerância numérica definida

#### Scenario: No future leakage
- **WHEN** as features de uma transação são calculadas
- **THEN** apenas transações com timestamp anterior (ou o próprio evento) contribuem para elas

### Requirement: Baseline and gradient-boosting comparison
O treinamento SHALL comparar um baseline simples (por exemplo, regra por limiar ou regressão logística) com ao menos um modelo de gradient boosting no mesmo split temporal, e SHALL reportar os resultados lado a lado.

#### Scenario: Comparative report
- **WHEN** o treinamento completo é executado
- **THEN** o relatório traz as métricas de todos os modelos comparados sobre o mesmo conjunto de teste

### Requirement: Imbalance-aware evaluation
A avaliação SHALL reportar Precision, Recall, F1, PR-AUC e False Positive Rate, e MUST NOT usar acurácia como métrica principal.

#### Scenario: Required metrics reported
- **WHEN** um modelo é avaliado
- **THEN** o relatório contém Precision, Recall, F1, PR-AUC e False Positive Rate no threshold operacional, além da taxa de fraude do conjunto de teste

### Requirement: Explicit decision threshold
O threshold de decisão SHALL ser escolhido explicitamente segundo um critério documentado (por exemplo, Recall-alvo sujeito a um teto de False Positive Rate), selecionado em dados de validação e não no conjunto de teste, e persistido junto ao modelo.

#### Scenario: Threshold selected without touching the test set
- **WHEN** o threshold é escolhido
- **THEN** ele deriva apenas de dados de validação e o conjunto de teste é usado só para a avaliação final

### Requirement: Versioned model artifact
O treinamento SHALL produzir um artefato contendo o modelo, a lista ordenada de features, o threshold, a versão e as métricas de avaliação, carregável pelo serviço de scoring sem retreino.

#### Scenario: Artifact loads in serving
- **WHEN** o serviço de scoring inicia com o artefato disponível
- **THEN** ele carrega modelo, features e threshold e reporta a versão do modelo carregada

### Requirement: Reference distribution for monitoring
O treinamento SHALL persistir estatísticas de referência das features e da distribuição de scores no conjunto de treino/validação, para permitir a detecção de drift em produção.

#### Scenario: Reference stats available
- **WHEN** o artefato é gerado
- **THEN** ele inclui as estatísticas de referência por feature e da distribuição de scores

### Requirement: Reproducible training run
O treinamento SHALL ser reprodutível a partir de uma seed e de uma configuração, e os resultados reportados no README SHALL corresponder a uma execução real desse processo.

#### Scenario: Re-running training
- **WHEN** o treinamento é executado novamente com a mesma seed, dados e configuração
- **THEN** as métricas reportadas são iguais ou diferem apenas dentro de uma tolerância documentada
