## Purpose

Dashboard público de demonstração e monitoramento: mostra o pipeline funcionando em tempo real, a saúde operacional e a qualidade do modelo, e deixa o visitante interagir enviando uma transação própria.

## ADDED Requirements

### Requirement: Live transaction feed
O dashboard SHALL exibir, em tempo real, as transações pontuadas com valor, merchant, localização, score e decisão, destacando visualmente as sinalizadas.

#### Scenario: New transactions appear without reload
- **WHEN** o dashboard está aberto e novas transações são pontuadas
- **THEN** elas aparecem no feed sem recarregar a página

### Requirement: Operational metrics panel
O dashboard SHALL exibir métricas operacionais em janela recente: transações por minuto, taxa de sinalização, latência de scoring (p50/p95) e distribuição de scores.

#### Scenario: Metrics refresh
- **WHEN** o dashboard está aberto por alguns minutos
- **THEN** as métricas são atualizadas periodicamente refletindo o tráfego recente

### Requirement: Model quality panel
O dashboard SHALL exibir as métricas offline do modelo (Precision, Recall, F1, PR-AUC, False Positive Rate), o threshold e a versão, e a comparação entre baseline e modelo de produção.

#### Scenario: Offline results visible
- **WHEN** o visitante abre a seção de qualidade do modelo
- **THEN** ele vê as métricas do relatório de avaliação e a comparação com o baseline

### Requirement: Drift monitoring
O dashboard SHALL exibir um indicador de drift comparando a distribuição recente de features e scores com a referência do treino, sinalizando quando o desvio ultrapassa um limite configurado.

#### Scenario: Drift injected in the feed
- **WHEN** o produtor de replay opera em um modo que desloca a distribuição de uma feature
- **THEN** o indicador de drift dessa feature sobe e sinaliza alerta acima do limite

#### Scenario: Stable distribution
- **WHEN** o tráfego segue a mesma distribuição do treino
- **THEN** o indicador permanece abaixo do limite de alerta

### Requirement: Try-it-yourself scoring
O dashboard SHALL permitir que o visitante monte uma transação (com exemplos prontos de uma legítima e de uma suspeita) e veja o score, a decisão e as features que mais pesaram.

#### Scenario: Suspicious preset scored
- **WHEN** o visitante escolhe o exemplo suspeito e envia
- **THEN** o dashboard mostra um score alto, a decisão e as features contribuintes

#### Scenario: Rate limit feedback
- **WHEN** o visitante excede o limite de requisições
- **THEN** o dashboard mostra uma mensagem clara de limite excedido em vez de falhar silenciosamente

### Requirement: Synthetic-data disclosure and navigation
O dashboard SHALL declarar claramente que os dados são sintéticos, oferecer link para o repositório e um link de volta ao site principal.

#### Scenario: Disclosure visible
- **WHEN** o visitante abre o dashboard
- **THEN** o aviso de dados sintéticos e os links estão visíveis sem interação adicional

### Requirement: CSP-compatible frontend
O frontend MUST NOT depender de script ou estilo inline, nem de recursos de terceiros, para funcionar sob a Content-Security-Policy do domínio principal (`script-src 'self'`, `style-src 'self'`).

#### Scenario: Loaded under strict CSP
- **WHEN** o dashboard é carregado sob a CSP do domínio principal
- **THEN** nenhum recurso é bloqueado e não há violações de CSP no console

### Requirement: Design consistent with the portfolio
O dashboard SHALL seguir o sistema visual do portfólio (paleta, tipografia e tema) e ser utilizável em telas de celular sem rolagem horizontal da página.

#### Scenario: Mobile viewport
- **WHEN** o dashboard é aberto em uma largura de celular
- **THEN** o layout se adapta e não há rolagem horizontal da página

### Requirement: Graceful degradation
Se o stream ao vivo ou a API ficarem indisponíveis, o dashboard SHALL indicar o estado de desconexão e tentar reconectar, sem exibir dados obsoletos como se fossem atuais.

#### Scenario: Stream disconnected
- **WHEN** a conexão do stream cai
- **THEN** o dashboard mostra o estado desconectado e reconecta automaticamente quando a API volta
