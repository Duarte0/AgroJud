# SPEC-007 — Jobs, leases e posse

Status: DONE

Milestone/Spike: M3

## Dependências

[SPEC-006](SPEC-006-movimentos-e-quarentena.md)

Referências: [Plano](../IMPLEMENTATION_PLAN.md) e [PRD](../PRD.md). Executar somente esta unidade; não implementar suas sucessoras.

## Objetivo
Controlar execução persistente com posse exclusiva, recuperação e cancelamento.

## Contexto
M3 agrega orquestração ao armazenamento de M2, antes de fazer chamadas paginadas.

## Escopo
Jobs, tentativas, eventos, checkpoint inicial e worker com handler de teste. Sem coletor de produção nesta unidade.

## Requisitos técnicos e contratos
- Job referencia coleta; tipos iniciais discovery e refresh_number, extensíveis por migrations futuras. Snapshot imutável dos parâmetros e chave canônica da operação.
- Estados: queued, running, retry_wait, completed, partial, failed, cancelled. Cobertura e reason são campos separados; completed não significa snapshot global da fonte.
- Reserva atômica de queued/retry_wait vencido com FOR UPDATE SKIP LOCKED; lease 120s, heartbeat 20s, token UUID por posse e relógio do banco.
- Índice único parcial da chave operacional nos estados queued/running/retry_wait. Chave inclui tipo, fonte, tribunal e consulta resolvida/sort; orçamento não altera identidade da busca.
- Heartbeat usa sessão própria em thread dedicada, sem HTTP concorrente. Perda de conexão ou posse suspende autorização de gravação.
- Qualquer transação de resultado bloqueia a linha do job, verifica token/lease/cancelamento antes de gravar e protege o commit contra nova posse concorrente.
- Revalidar expiração com relógio corrente do banco antes de confirmar progresso; não usar timestamp congelado no início da transação para essa verificação. Se a lease venceu durante a gravação, rollback integral, mesmo sem outro consumidor ainda ter assumido.
- Checkpoint inicial guarda cursor nulo, próxima página e revisão de progresso. Atualização compare-and-swap da revisão impede replay tardio.
- Ordem de locks: job antes de representação; representações em ordem de ID. Nenhum HTTP com transação aberta.
- Cancelar queued/retry_wait muda imediatamente para cancelled. Em running, sinalizar cancel_requested; transação já em commit pode terminar antes do cancelamento adquirir lock. Após confirmação do cancelamento, nenhuma nova página confirma.
- Lease vencida permite reserva nova sem apagar tentativas ou checkpoint. Token antigo é invalidado.
- Comandos internos: enqueue, claim, heartbeat, request_cancel, finish e inspect. Retornar job existente em disputa de criação equivalente.
- statement_timeout 30s e lock_timeout 5s para transações de ingestão; executar rollback em violação.

## Comportamento esperado
Worker morto deixa execução recuperável. Cancelamento e commit têm ordem transacional definida; não prometer desfazer página já confirmada.

## Decisões importantes
Dois workers apenas em testes. Retomada/continuação públicas ficam na SPEC-009; a fila já reserva os estados necessários. Migrations acrescentam FK para coleta sem invalidar observações de M2.

## Implementação entregue

- A migration `20261009_0004` acrescenta `jobs`, `job_attempts`, `job_events` e `job_checkpoints`, com FK para `collections` compatível com as revisões da SPEC-006. Constraints impõem estados, tipo, lease consistente, revisão de checkpoint e unicidade por tentativa/evento. Índices parciais atendem reserva e unicidade operacional ativa. Triggers protegem o snapshot de parâmetros e tornam eventos append-only; tentativas só podem ser encerradas uma vez.
- `agrojud.services.jobs.JobService` implementa `enqueue`, `claim`, `heartbeat`, `request_cancel`, `finish` e `inspect`, além da gravação protegida e avanço CAS do checkpoint. A chave operacional usa tipo, fonte, tribunal, consulta resolvida e sort; parâmetros de orçamento ficam no snapshot imutável e não alteram a chave. Criação equivalente retorna o job ativo e reverte a collection concorrente não utilizada.
- `claim` combina `FOR UPDATE SKIP LOCKED` com `clock_timestamp()`, cria token UUID por tentativa, recupera lease vencida sem apagar tentativas/checkpoint e finaliza como cancelado um job expirado com cancelamento solicitado. Heartbeat usa sua própria sessão; a falha local suspende gravações. Transações protegidas bloqueiam primeiro o job, verificam posse/cancelamento e revalidam o relógio corrente do PostgreSQL após os writes, imediatamente antes do commit; lease vencida reverte a transação toda.
- `LeasedWorker` executa handlers injetados fora de transações, mantém heartbeat em thread dedicada, sinaliza cancelamento e perda de posse, e registra erros inesperados com resumo sanitizado. O processo principal fica ocioso sem handlers de produção, conforme a exclusão de coletor desta SPEC. Lease, heartbeat e polling são configuráveis por ambiente, com defaults de 120s, 20s e 2s.
- O checkpoint nasce com cursor nulo, página 1 e revisão 0. Atualização exige revisão esperada e avanço de página. Cancelar estado queued/retry_wait é imediato; em running persiste `cancel_requested`, serializado pelo lock do job com qualquer commit em andamento.

## Critérios de aceitação
- **AC1 — PASSOU:** duas sessões concorrentes criaram uma única execução equivalente mesmo com orçamentos diferentes; a collection excedente foi revertida. Dois claims concorrentes produziram uma única posse. O índice parcial permite nova execução após terminalização.
- **AC2 — PASSOU:** recuperação criou token e tentativa novos sem remover checkpoint/histórico; gravação com token antigo falhou. Relógio avançado durante uma transação protegida reverteu a alteração do checkpoint mesmo sem outro consumidor.
- **AC3 — PASSOU:** handler bloqueado observou heartbeat confirmado por sessão independente. Falha simulada de heartbeat sinalizou perda de posse e impediu escrita local; não presumiu posse válida.
- **AC4 — PASSOU:** cancelamento esperou transação que já possuía o lock, preservou o checkpoint confirmado e, após confirmar, rejeitou novo avanço. `queued` foi cancelado imediatamente; `retry_wait` só foi reservado após vencer `available_at`.
- **AC5 — PASSOU:** nova instância do serviço recuperou o job após lease expirada e leu tentativas, eventos ordenados e checkpoint persistidos. Falha inesperada do handler foi registrada sem mensagem bruta.

## Testes necessários
PostgreSQL com duas sessões/barreiras para AC1/AC2/AC4; relógio avançado na base ou lease curta de teste. AC3: handler bloqueado e heartbeat independente. AC5: nova instância do worker/serviço com job de diagnóstico persistido.

## Erros e edge cases
Erro de banco durante heartbeat não gera posse presumida. Handler inesperadamente falho termina tentativa com erro sanitizado. Reserva vazia é worker ocioso, não sucesso de coleta.

## Fora do escopo
HTTP DataJud, backoff operacional, escalabilidade multiworker em produção e scheduler.

## Evidência e conclusão

Validação em PostgreSQL 18.6 isolado, Python 3.14.8 e fixtures locais: migration aplicada em banco vazio e a partir da revisão `20261009_0003` com collection preexistente; 142 testes passaram. Ruff check, Ruff format check e mypy passaram. Compose demo/real/test foi validado; um projeto Compose isolado subiu API e banco, aplicou migrations e passou `/health/live` e `/health/ready`. A imagem de produção API/worker foi construída e `agrojud-worker --check` passou.

O teste de concorrência usou duas sessões PostgreSQL e barreiras. O teste de lease expirando durante gravação confirmou rollback integral; o de cancelamento confirmou a ordem transacional entre gravação em andamento e sinalização. A evidência cobre infraestrutura local com handlers de teste; não houve chamada ao DataJud nem ingestão de produção. SPEC-008/009 continuam responsáveis por paginação, retries e recuperação ponta a ponta.

Comandos principais de validação:

```sh
TEST_DB_HOST_PORT=55498 docker compose --project-name agrojud-spec007-final --env-file .env.test -f compose.test.yaml --profile test up -d db
TEST_DB_HOST_PORT=55498 docker compose --project-name agrojud-spec007-final --env-file .env.test -f compose.test.yaml --profile test build tests
TEST_DB_HOST_PORT=55498 docker compose --project-name agrojud-spec007-final --env-file .env.test -f compose.test.yaml --profile test run --rm --no-deps -v "$PWD/backend/src:/app/src" -v "$PWD/backend/tests:/app/tests" tests uv run --no-sync ruff check src tests
TEST_DB_HOST_PORT=55498 docker compose --project-name agrojud-spec007-final --env-file .env.test -f compose.test.yaml --profile test run --rm --no-deps -v "$PWD/backend/src:/app/src" -v "$PWD/backend/tests:/app/tests" tests uv run --no-sync ruff format --check src tests
TEST_DB_HOST_PORT=55498 docker compose --project-name agrojud-spec007-final --env-file .env.test -f compose.test.yaml --profile test run --rm --no-deps -v "$PWD/backend/src:/app/src" -v "$PWD/backend/tests:/app/tests" tests uv run --no-sync mypy src
TEST_DB_HOST_PORT=55498 docker compose --project-name agrojud-spec007-final --env-file .env.test -f compose.test.yaml --profile test run --rm --no-deps -v "$PWD/backend/src:/app/src" -v "$PWD/backend/tests:/app/tests" tests uv run --no-sync pytest
docker compose --project-name agrojud-spec007-demo --env-file .env.demo.example -f compose.yaml config --quiet
docker compose --project-name agrojud-spec007-real --env-file .env.real.example -f compose.yaml config --quiet
docker compose --project-name agrojud-spec007-test --env-file .env.test.example -f compose.test.yaml --profile test config --quiet
docker compose --project-name agrojud-spec007-build --env-file .env.demo.example -f compose.yaml build api worker
docker compose --project-name agrojud-spec007-build --env-file .env.demo.example -f compose.yaml run --rm --no-deps worker uv run --no-sync agrojud-worker --check
API_PORT=18087 docker compose --project-name agrojud-spec007-smoke --env-file .env.demo.example -f compose.yaml up --build --detach db api
API_PORT=18087 docker compose --project-name agrojud-spec007-smoke --env-file .env.demo.example -f compose.yaml run --rm api uv run --no-sync alembic upgrade head
API_PORT=18087 docker compose --project-name agrojud-spec007-smoke --env-file .env.demo.example -f compose.yaml up --detach --wait api
API_PORT=18087 docker compose --project-name agrojud-spec007-smoke --env-file .env.demo.example -f compose.yaml run --rm --no-deps worker uv run --no-sync agrojud-worker --check
curl --fail --silent --show-error http://127.0.0.1:18087/api/v1/health/live
curl --fail --silent --show-error http://127.0.0.1:18087/api/v1/health/ready
API_PORT=18087 docker compose --project-name agrojud-spec007-smoke --env-file .env.demo.example -f compose.yaml stop api db
git diff --check
```
