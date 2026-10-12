## MODIFIED Requirements

### Requirement: Live transaction feed
O dashboard SHALL exibir, em tempo real, as transações pontuadas com valor, merchant, localização, score e decisão, destacando visualmente as sinalizadas, sem exibir a mesma transação mais de uma vez mesmo quando ela chega tanto pela carga inicial via REST quanto pelo stream ao vivo.

#### Scenario: New transactions appear without reload
- **WHEN** o dashboard está aberto e novas transações são pontuadas
- **THEN** elas aparecem no feed sem recarregar a página

#### Scenario: No duplicate entries across initial load and live stream
- **WHEN** uma transação já presente na carga inicial do feed também chega pelo stream ao vivo logo em seguida
- **THEN** ela aparece no feed uma única vez
