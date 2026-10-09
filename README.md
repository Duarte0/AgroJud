# AgroJud Radar

Fundação local do monitor de contencioso do produtor rural. A SPEC-001 entrega API e worker mínimos, PostgreSQL real, migrations, saúde e CI. A SPEC-002 acrescenta os contratos e adaptadores de fonte; a API e o worker ainda não iniciam coleta de produto.

## Requisitos locais

- Docker Engine e Docker Compose v2.
- `curl` para os probes HTTP documentados.
- Para comandos Python no host: uv **0.12.20** e CPython **3.14.8**. O fluxo Compose usa a mesma versão fixada e não depende do Python global.

Instale a versão documentada do uv pelo instalador oficial:

```sh
curl --proto '=https' --tlsv1.2 -LsSf https://releases.astral.sh/github/uv/releases/download/0.12.20/uv-installer.sh | sh
```

Instale CPython 3.14.8 pelo gerenciador oficial do sistema quando quiser executar ferramentas no host. Os comandos Compose não dependem dessa instalação local.

As versões escolhidas foram conferidas em 08/10/2026: Python 3.14.8 está em manutenção estável, PostgreSQL 18.6 é a versão minor atual suportada, e o uv é fixado em 0.12.20. Consulte a [política de versões do Python](https://devguide.python.org/versions/), a [política de versões do PostgreSQL](https://www.postgresql.org/support/versioning/) e o [lockfile do uv](https://docs.astral.sh/uv/guides/projects/).

## Configurar e subir a demonstração

```sh
cp .env.demo.example .env.demo
docker compose --project-name agrojud-demo --env-file .env.demo -f compose.yaml up --build --detach db api
docker compose --project-name agrojud-demo --env-file .env.demo -f compose.yaml run --rm api uv run --no-sync alembic upgrade head
curl --fail http://127.0.0.1:8000/api/v1/health/live
curl --fail http://127.0.0.1:8000/api/v1/health/ready
```

API publica apenas em `127.0.0.1:8000`; o banco não publica porta no host. `API_PORT` permite escolher outra porta quando 8000 estiver ocupada. Migrações são explícitas e devem ser aplicadas após subir o banco. A imagem API e o comando worker usam o mesmo Dockerfile e pacote.

Para validar também o comando inicial do worker:

```sh
docker compose --project-name agrojud-demo --env-file .env.demo -f compose.yaml run --rm worker
```

O worker valida ambiente e credenciais e encerra; não consulta fonte nem fica em loop.

## Contratos de fonte (SPEC-002)

`agrojud.sources` oferece consultas imutáveis TJGO, erros tipados, páginas com hits brutos e cursores preservados, além dos adaptadores HTTP DataJud e sintético. O adaptador HTTP faz uma tentativa por chamada, usa timeouts connect/read/write/pool de 5/20/20/5 segundos e não segue redirecionamentos. O endpoint é fixo em TJGO; a interface não aceita DSL livre.

O seletor de fonte exige escolha explícita compatível com o ambiente: `demo` usa apenas fixtures sintéticas e `real` usa apenas DataJud com `DATAJUD_API_KEY`. Falhas de DataJud não acionam fallback sintético. Em `test`, o adaptador DataJud exige transporte HTTP simulado. O worker ainda não chama o seletor nem processa consultas.

O contrato e o transporte foram validados com HTTPX MockTransport. Isso não comprova o shape real dos hits, a correspondência histórica entre IDs, a ordenação composta ou a paginação do TJGO; esses pontos seguem sob SPEC-003.

## Probe limitada DataJud/TJGO (SPEC-003)

O comando `agrojud-datajud-probe` executa uma consulta diagnóstica sem gravar no banco de produto. Exige `AGROJUD_ENV=real` e `DATAJUD_API_KEY` no ambiente do backend. Faz até seis requisições, limita cada página a 100 hits, não repete automaticamente e grava um JSON sanitizado no caminho indicado. Por padrão consulta os últimos 365 dias; `--from-date` e `--to-date` definem outro intervalo semiaberto.

Com `.env.real` configurado, monte a pasta de evidências para guardar o relatório no repositório:

```sh
mkdir -p docs/evidence
docker compose --project-name agrojud-real --env-file .env.real -f compose.yaml run --rm --no-deps -v "$PWD/docs/evidence:/evidence" api uv run --no-sync agrojud-datajud-probe --output /evidence/datajud-tjgo-validacao.json
```

Timeout ou indisponibilidade gera diagnóstico `INCONCLUSIVE`, nunca resultado vazio nem habilitação da fonte real. O comando só pesquisa por CNJ depois de observar o número em um hit público. A SPEC-003 registra a matriz e os limites atuais em [sua evidência](docs/evidence/datajud-tjgo-validacao-2026-10-08.json); o uso da API também permanece sujeito ao termo registrado no [PRD](PRD.md#2-decisões-e-premissas).

## Ambientes separados

Crie `.env.real` a partir de `.env.real.example`, substitua os valores de exemplo por credenciais locais e escolha `API_PORT` disponível. Suba com um projeto Compose e um volume próprios:

```sh
cp .env.real.example .env.real
docker compose --project-name agrojud-real --env-file .env.real -f compose.yaml up --build --detach db api
docker compose --project-name agrojud-real --env-file .env.real -f compose.yaml run --rm api uv run --no-sync alembic upgrade head
```

O projeto `agrojud-demo` usa o banco `agrojud_demo` e o projeto `agrojud-real` usa `agrojud_real`; os nomes Compose distintos isolam rede e volume. Os arquivos locais `.env.*` não são versionados. `DATAJUD_API_KEY` é lida somente no backend e usada pelo adaptador DataJud no ambiente `real`; não é exposta ao frontend nem registrada nos logs.

Pare e retome sem remover o volume:

```sh
docker compose --project-name agrojud-demo --env-file .env.demo -f compose.yaml stop
docker compose --project-name agrojud-demo --env-file .env.demo -f compose.yaml start
```

## Testes, lint e typecheck

O banco de teste é um projeto independente, usa o banco `agrojud_test` e publica PostgreSQL apenas em `127.0.0.1:55432`. Seu volume não é compartilhado com demo ou real.

```sh
cp .env.test.example .env.test
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

`./scripts/smoke-compose.sh demo` sobe a API, aplica a migration, valida liveness/readiness e executa o worker inicial. Passe `real` para validar a configuração do ambiente real. Nenhum comando de reset ou exclusão de volume é necessário para desenvolvimento ou validação.
