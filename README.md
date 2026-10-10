# AgroJud Radar

Aplicação local de monitoramento e triagem de processos públicos relacionados ao agronegócio. O Compose completo inclui frontend, API, worker e PostgreSQL persistente. A demonstração usa fixtures sintéticas; DataJud/TJGO permanece desabilitado enquanto as capacidades externas necessárias não forem aprovadas. Consulte o [runbook de operação e aceite](docs/operacao-e-aceite-integrado.md) para arquitetura, semântica dos dados, backup/restauração, reset demo e limites.

## Requisitos locais

- Docker Engine e Docker Compose v2.
- `curl` para os probes HTTP documentados.
- Para comandos Python no host: uv **0.12.22** e CPython **3.14.8**. O fluxo Compose usa a mesma versão fixada e não depende do Python global.
- Node.js **26.8.1** para os testes/build frontend executados no host; o container de produção constrói os assets sem instalar Node no host.

Instale a versão documentada do uv pelo instalador oficial:

```sh
curl --proto '=https' --tlsv1.2 -LsSf https://releases.astral.sh/github/uv/releases/download/0.12.22/uv-installer.sh | sh
```

Instale CPython 3.14.8 pelo gerenciador oficial do sistema quando quiser executar ferramentas no host. Os comandos Compose não dependem dessa instalação local.

As versões escolhidas foram conferidas em 08/10/2026: Python 3.14.8 está em manutenção estável, PostgreSQL 18.6 é a versão minor atual suportada, e o uv é fixado em 0.12.22 (atualizado em 09/10/2026: o 0.12.20 não oferece download gerenciado do CPython 3.14.8). Consulte a [política de versões do Python](https://devguide.python.org/versions/), a [política de versões do PostgreSQL](https://www.postgresql.org/support/versioning/) e o [lockfile do uv](https://docs.astral.sh/uv/guides/projects/).

## Configurar e subir a demonstração

```sh
if [ ! -f .env.demo ]; then cp .env.demo.example .env.demo; fi
docker compose --project-name agrojud-demo --env-file .env.demo -f compose.yaml up --detach --wait db
docker compose --project-name agrojud-demo --env-file .env.demo -f compose.yaml run --rm --no-deps api uv run --no-sync alembic upgrade head
docker compose --project-name agrojud-demo --env-file .env.demo -f compose.yaml --profile worker up --build --detach --wait api frontend worker
curl --fail http://127.0.0.1:5173/
curl --fail http://127.0.0.1:8000/api/v1/health/live
curl --fail http://127.0.0.1:8000/api/v1/health/ready
```

Frontend publica apenas em `127.0.0.1:5173` e API em `127.0.0.1:8000`; PostgreSQL fica somente na rede interna. `FRONTEND_PORT` e `API_PORT` permitem escolher outras portas. O Nginx encaminha `/api/` à API e serve o fallback SPA para rotas como `/processes`. Migrações são explícitas e devem ser aplicadas após subir o banco. API e worker usam a mesma imagem backend.

O worker executa um loop persistente com handlers para jobs `discovery` e `refresh_number`. No ambiente `demo`, as consultas usam fixtures determinísticas marcadas como sintéticas. O ambiente `real` permanece bloqueado pela API enquanto S1/S2 não forem aprovados. Para validar configuração e encerrar sem iniciar o loop:

```sh
docker compose --project-name agrojud-demo --env-file .env.demo -f compose.yaml --profile worker run --rm --no-deps worker uv run --no-sync agrojud-worker --check
```

O worker lê as opções `JOB_LEASE_SECONDS` (120), `JOB_HEARTBEAT_SECONDS` (20) e `JOB_POLL_SECONDS` (2) do ambiente. Heartbeats usam uma sessão PostgreSQL própria. O modo `--check` valida configuração sem acessar o banco.

## Contratos de fonte (SPEC-002)

`agrojud.sources` oferece consultas imutáveis TJGO, erros tipados, páginas com hits brutos e cursores preservados, além dos adaptadores HTTP DataJud e sintético. O adaptador HTTP faz uma tentativa por chamada, usa timeouts connect/read/write/pool de 5/20/20/5 segundos e não segue redirecionamentos. O endpoint é fixo em TJGO; a interface não aceita DSL livre.

O seletor de fonte exige escolha explícita compatível com o ambiente: `demo` usa apenas fixtures sintéticas e `real` usa apenas DataJud com `DATAJUD_API_KEY`. Falhas de DataJud não acionam fallback sintético. Em `test`, o adaptador DataJud exige transporte HTTP simulado. O worker usa o seletor para processar jobs; a API mantém a criação no ambiente real desabilitada enquanto as evidências externas necessárias estiverem inconclusivas.

O contrato e o transporte foram validados com HTTPX MockTransport. Isso não comprova o shape real dos hits, a correspondência histórica entre IDs, a ordenação composta ou a paginação do TJGO; esses pontos seguem sob SPEC-003.

## API operacional e OpenAPI (SPEC-011)

A API expõe criação, listagem, detalhe e comandos de jobs em `/api/v1/jobs`, além de processos, representações, movimentos, triagem, sinais, acompanhados, presets e ambiente. A lista `/api/v1/watchlist` é paginada; inclusão/remoção usa `/api/v1/processes/{id}/watch`, e a atualização por número em `/api/v1/processes/{id}/refresh` exige processo local acompanhado ativo. Consultas usam paginação local estável; campos brutos completos e cursores remotos não são retornados por padrão. Erros têm estrutura uniforme e `X-Request-ID`. A documentação interativa fica em `http://127.0.0.1:8000/api/v1/docs`, e o contrato JSON em `/api/v1/openapi.json`.

Exemplo de criação de coleta sintética no ambiente demo:

```sh
curl --fail-with-body -X POST http://127.0.0.1:8000/api/v1/jobs \
  -H 'Content-Type: application/json' \
  -d '{"kind":"discovery","criteria":{"preset_id":"rural.credito_contratos"}}'
```

A resposta HTTP 202 inclui o identificador persistido do job e o cabeçalho `Location`. Consulte esse endereço para acompanhar o estado; o worker separado processa a fila PostgreSQL.

Para gerar ou conferir o contrato e os tipos TypeScript, consulte [frontend/README.md](frontend/README.md). A CI executa `npm run openapi:check` sem chamadas ao DataJud.

## Interface de coleta e consulta (SPEC-012)

O container frontend serve a interface em `http://127.0.0.1:5173`. Para desenvolvimento fora do Compose, a API demo deve estar em `127.0.0.1:8000`:

```sh
cd frontend
npm ci
npm run dev
```

A interface tem radar, coletas e processos, com barra permanente indicando demo/real. O servidor Vite de desenvolvimento publica somente em `127.0.0.1` e encaminha `/api` para a API local. O fluxo Playwright sobe a imagem frontend e valida proxy/fallback em stack demo isolada com banco efêmero. Detalhes em [frontend/README.md](frontend/README.md).

## Probe limitada DataJud/TJGO (SPEC-003)

O comando `agrojud-datajud-probe` executa uma consulta diagnóstica sem gravar no banco de produto. Exige `AGROJUD_ENV=real` e `DATAJUD_API_KEY` no ambiente do backend. Faz até seis requisições, limita cada página a 100 hits, não repete automaticamente e grava um JSON sanitizado no caminho indicado. Por padrão consulta os últimos 365 dias; `--from-date` e `--to-date` definem outro intervalo semiaberto.

Com `.env.real` configurado, monte a pasta de evidências para guardar o relatório no repositório:

```sh
mkdir -p docs/evidence
docker compose --project-name agrojud-real --env-file .env.real -f compose.yaml run --rm --no-deps -v "$PWD/docs/evidence:/evidence" api uv run --no-sync agrojud-datajud-probe --output /evidence/datajud-tjgo-validacao.json
```

Timeout ou indisponibilidade gera diagnóstico `INCONCLUSIVE`, nunca resultado vazio nem habilitação da fonte real. O comando só pesquisa por CNJ depois de observar o número em um hit público. A matriz da probe está em [sua evidência](docs/evidence/datajud-tjgo-validacao-2026-10-08.json); a [amostra manual sanitizada](docs/evidence/datajud-tjgo-amostra-manual-2026-10-09.json) confirma apenas o envelope e alguns campos de um hit. O uso da API também permanece sujeito ao termo registrado no [PRD](PRD.md#2-decisões-e-premissas).

## Ambientes separados

Crie `.env.real` a partir de `.env.real.example`, configure credenciais somente localmente e escolha `API_PORT` e `FRONTEND_PORT` disponíveis. Suba projeto e volume próprios; migrations continuam explícitas:

```sh
if [ ! -f .env.real ]; then cp .env.real.example .env.real; fi
docker compose --project-name agrojud-real --env-file .env.real -f compose.yaml up --detach --wait db
docker compose --project-name agrojud-real --env-file .env.real -f compose.yaml run --rm --no-deps api uv run --no-sync alembic upgrade head
docker compose --project-name agrojud-real --env-file .env.real -f compose.yaml up --build --detach --wait api frontend
```

O frontend real fica em `http://127.0.0.1:5174`; a API em `http://127.0.0.1:8001`. O PostgreSQL não publica porta no host. Para uma inspeção administrativa local, use o próprio container:

```sh
docker compose --project-name agrojud-real --env-file .env.real -f compose.yaml exec -T db sh -lc 'psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB"'
```

O projeto `agrojud-demo` usa o banco `agrojud_demo` e o projeto `agrojud-real` usa `agrojud_real`; os nomes Compose distintos isolam rede e volume. Os arquivos locais `.env.*` não são versionados. `DATAJUD_API_KEY` é lida somente no backend e usada pelo adaptador DataJud no ambiente `real`; não é exposta ao frontend nem registrada nos logs.

Pare e retome sem remover o volume:

```sh
docker compose --project-name agrojud-demo --profile worker --env-file .env.demo -f compose.yaml stop
docker compose --project-name agrojud-demo --profile worker --env-file .env.demo -f compose.yaml start
```

O fluxo de backup, restore descartável e reset explícito da demo está em [operação e aceite integrado](docs/operacao-e-aceite-integrado.md#backup-restauração-e-reset). Nunca remova volumes para resolver migration pendente.

## Testes, lint e typecheck

O banco de teste é um projeto independente, usa o banco `agrojud_test` e publica PostgreSQL apenas em `127.0.0.1:55432`. Seu volume não é compartilhado com demo ou real.

```sh
if [ ! -f .env.test ]; then cp .env.test.example .env.test; fi
docker compose --project-name agrojud-test --env-file .env.test -f compose.test.yaml --profile test run --rm --build tests uv run --no-sync ruff check src tests
docker compose --project-name agrojud-test --env-file .env.test -f compose.test.yaml --profile test run --rm tests uv run --no-sync ruff format --check src tests
docker compose --project-name agrojud-test --env-file .env.test -f compose.test.yaml --profile test run --rm tests uv run --no-sync mypy src
docker compose --project-name agrojud-test --env-file .env.test -f compose.test.yaml --profile test run --rm tests uv run --no-sync pytest
```

Os testes criam bancos temporários com sufixo `_test`, verificam as respostas HTTP e os estados de migration sem tocar em banco operacional. A configuração rejeita `TEST_DATABASE_URL` idêntica a `DATABASE_URL` e qualquer banco de teste sem sufixo `_test`. Falha ao conectar ao PostgreSQL faz a suíte falhar; não há skip automático.

Para repetir essas validações pelo script local:

```sh
./scripts/verify-persistence.sh
```

O script grava um registro diagnóstico somente em PostgreSQL com `AGROJUD_ENV=test`, para o banco reiniciar sem perder o volume, consulta o registro e remove a tabela diagnóstica ao final. Se uma etapa falhar, o script preserva o estado para inspeção.

## Comandos úteis

```sh
docker compose --project-name agrojud-demo --env-file .env.demo -f compose.yaml ps
docker compose --project-name agrojud-demo --env-file .env.demo -f compose.yaml logs api
docker compose --project-name agrojud-demo --env-file .env.demo -f compose.yaml stop
docker compose --project-name agrojud-demo --env-file .env.demo -f compose.yaml start
```

`./scripts/smoke-compose.sh demo` constrói e valida frontend, proxy SPA/API, API, worker e PostgreSQL demo. Passe `real` somente para validar o stack real sem iniciar worker ou executar coleta. O reset demo exige `./scripts/reset-demo.sh --target demo --confirm`; não existe reset para o ambiente real.
