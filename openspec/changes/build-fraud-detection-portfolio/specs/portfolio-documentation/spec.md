## Purpose

Torna o projeto apresentável a recrutadores: um README que responde às perguntas típicas de entrevista com resultados reais, e os artefatos necessários para listar o projeto e a demo no site matheusramos.dev.

## ADDED Requirements

### Requirement: Recruiter-friendly README
O README SHALL responder, nesta ordem lógica: qual problema o projeto resolve, como funciona, qual a arquitetura, quais tecnologias foram usadas, quais os resultados e como executar; e SHALL destacar o link da demo ao vivo.

#### Scenario: Recruiter skims the README
- **WHEN** um recrutador lê o README
- **THEN** encontra o link da demo, o diagrama de arquitetura, a tabela de resultados e as instruções de execução sem precisar abrir o código

### Requirement: Architecture diagrams
O README SHALL incluir diagrama do pipeline (transação até dashboard) e diagrama de deploy (Cloudflare, Nginx, stack na VPS).

#### Scenario: Diagrams render
- **WHEN** o README é visualizado no GitHub
- **THEN** os diagramas são renderizados e refletem a arquitetura realmente implantada

### Requirement: Results grounded in real runs
As métricas do README (Precision, Recall, F1, PR-AUC, False Positive Rate, comparação baseline vs. modelo, latência de scoring) SHALL vir de execuções reais do projeto, com a metodologia (dados sintéticos, split temporal, seed) descrita, e as limitações SHALL ser declaradas.

#### Scenario: Numbers traceable
- **WHEN** o leitor quer verificar um número do README
- **THEN** existe o comando ou script que o reproduz e a metodologia está descrita

### Requirement: Trade-offs documented
O README SHALL documentar as principais decisões de design e seus trade-offs (incluindo escolha do modelo, do threshold, do streaming e das restrições de recursos da VPS), de forma que possam ser defendidas numa entrevista.

#### Scenario: Interview question answered
- **WHEN** o leitor procura por que uma decisão foi tomada (por exemplo, o threshold ou o dimensionamento do broker)
- **THEN** a seção de trade-offs explica a alternativa considerada e o motivo da escolha

### Requirement: Honest scope statement
A documentação SHALL declarar que os dados são sintéticos e que o modelo não é apto a uso com dados financeiros reais, sem sugerir desempenho em produção real.

#### Scenario: Limitations stated
- **WHEN** o leitor procura as limitações
- **THEN** encontra o aviso de dados sintéticos e as limitações conhecidas

### Requirement: Portfolio site integration artifacts
O repositório SHALL conter o conteúdo pronto para registrar o projeto no site do portfólio (título, descrição, tags, `repoUrl`, `demoUrl` apontando para `https://matheusramos.dev/projects/fraud/`, data) e o snippet Nginx correspondente, com instruções de aplicação no repositório `portfolio-website`.

#### Scenario: Owner applies the integration
- **WHEN** o dono segue as instruções nos artefatos
- **THEN** o projeto aparece na lista de projetos do site com link para a demo, sem editar nada além do que as instruções descrevem
