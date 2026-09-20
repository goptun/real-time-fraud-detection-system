## Purpose

Define como o sistema roda em produção na VPS Oracle compartilhada: empacotamento reproduzível, isolamento em relação aos outros projetos, exposição pública em `matheusramos.dev/projects/fraud/` e um processo repetível de deploy, verificação e rollback.

## ADDED Requirements

### Requirement: Reproducible container stack
O sistema completo (broker, banco, serviço de scoring, produtor de replay e API) SHALL subir com um único comando de orquestração de contêineres a partir de um clone do repositório e de um arquivo de ambiente, com serviços com política de reinício automático.

#### Scenario: Fresh clone brought up
- **WHEN** o repositório é clonado, o arquivo de ambiente é preenchido e o comando de subida é executado
- **THEN** todos os serviços iniciam e o health check da API reporta saudável

### Requirement: Isolation from other stacks on the shared VPS
O stack SHALL usar um nome de projeto de orquestração explícito, volumes e contêineres próprios e portas ligadas apenas a `127.0.0.1`, de modo que subir, atualizar ou remover este stack MUST NOT afetar os stacks do `rag-knowledge-assistant` e da newsletter.

#### Scenario: Deploying does not disturb other stacks
- **WHEN** este stack é implantado, reiniciado ou removido (incluindo remoção de órfãos)
- **THEN** os contêineres e volumes dos outros projetos continuam rodando e intactos

#### Scenario: Only the reverse proxy is public
- **WHEN** um cliente externo tenta acessar diretamente as portas do broker, do banco ou da API pelo IP público da VPS
- **THEN** a conexão é recusada; só o Nginx (portas 80/443) é alcançável

### Requirement: Bounded resource usage
Cada serviço SHALL ter limites de memória configurados e o dimensionamento SHALL ser validado contra a memória livre real da VPS antes do deploy, de forma que os stacks existentes continuem estáveis.

#### Scenario: Memory validated before deploy
- **WHEN** o deploy é preparado
- **THEN** a memória disponível da VPS e o consumo dos stacks existentes foram medidos e o total configurado para este stack cabe com margem documentada

#### Scenario: Stack under sustained load
- **WHEN** o stack roda continuamente com o produtor de replay ativo
- **THEN** o consumo de memória permanece estável dentro dos limites, sem reinícios por falta de memória

### Requirement: Public exposure under a sub-path
O sistema SHALL ser acessível em `https://matheusramos.dev/projects/fraud/` via Nginx com um novo bloco de localização que remove o prefixo, redireciona `/projects/fraud` para `/projects/fraud/` e mantém o stream de eventos sem buffering, sem alterar os blocos existentes do site.

#### Scenario: Demo reachable over HTTPS
- **WHEN** um visitante abre `https://matheusramos.dev/projects/fraud/`
- **THEN** o dashboard carrega e o stream ao vivo funciona

#### Scenario: Existing routes unaffected
- **WHEN** o novo bloco é aplicado
- **THEN** `/`, `/projects/rag/` e `/api/newsletter/` continuam respondendo como antes

### Requirement: Safe Nginx change procedure
A alteração da configuração do Nginx SHALL ser validada com o teste de sintaxe antes do reload, e o snippet autoritativo SHALL ficar versionado neste repositório.

#### Scenario: Invalid config not applied
- **WHEN** a configuração nova falha no teste de sintaxe
- **THEN** o reload não é executado e o Nginx continua servindo a configuração anterior

### Requirement: Secrets kept out of version control
Credenciais (senha do banco, chaves) SHALL vir de variáveis de ambiente em arquivo não versionado, e o repositório SHALL conter apenas um arquivo de exemplo com valores fictícios.

#### Scenario: Repository scanned for secrets
- **WHEN** o repositório é inspecionado
- **THEN** não há credenciais reais versionadas e o arquivo de ambiente real está no gitignore

### Requirement: Data persistence and retention
Os dados do banco e do broker SHALL persistir em volumes nomeados entre reinícios, e uma política de retenção SHALL limitar o crescimento de transações e do log do broker.

#### Scenario: Restart keeps data
- **WHEN** os contêineres são reiniciados
- **THEN** o histórico recente de transações continua disponível

#### Scenario: Retention bounds growth
- **WHEN** o replay roda por dias
- **THEN** registros mais antigos que a janela de retenção são removidos e o uso de disco permanece limitado

### Requirement: Documented deploy, verification and rollback
O repositório SHALL documentar o deploy passo a passo, a verificação pós-deploy (health, stream, scoring de exemplo, rotas vizinhas intactas) e o rollback para a versão anterior.

#### Scenario: Post-deploy verification
- **WHEN** o operador segue o procedimento documentado após um deploy
- **THEN** ele confirma via health, scoring de exemplo e stream que o sistema está operante

#### Scenario: Rolling back
- **WHEN** um deploy quebra a demo
- **THEN** o operador consegue voltar à versão anterior seguindo a documentação, sem afetar os outros stacks
