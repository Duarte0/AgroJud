# SPEC-001 — Fundação local

Status: DONE

Milestone/Spike: M0

## Dependências

Nenhuma.

Referências: [Plano](../IMPLEMENTATION_PLAN.md) e [PRD](../PRD.md). Executar somente esta unidade; não implementar suas sucessoras.

## Objetivo
Entregar uma base local reproduzível para executar API, worker e testes com PostgreSQL real.

## Contexto
M0 antecede qualquer coleta. A fundação não cria tabelas de domínio para simular progresso.

## Escopo
Pacote backend, configuração, imagem compartilhada, Compose, migrations, endpoints de saúde e CI. Reservar frontend com documentação; aplicação React pertence à SPEC-012.

## Requisitos técnicos e contratos
- Usar Python, PostgreSQL, FastAPI, Pydantic, SQLAlchemy, Psycopg, Alembic, HTTPX, pytest e Ruff, em versões estáveis, suportadas e mutuamente compatíveis. O PostgreSQL deve oferecer JSONB e `FOR UPDATE SKIP LOCKED`, capacidades usadas nos contratos do produto.
- Na implementação, consultar a documentação oficial para verificar suporte e compatibilidade; registrar e fixar as versões efetivamente utilizadas nos arquivos de dependências e lockfiles, Dockerfiles e configuração do Compose, incluindo imagens por versão ou digest. Não usar tags flutuantes. Aplicar o mesmo critério a runtimes e bibliotecas introduzidos por SPECs posteriores.
- Pacote em `backend/src/agrojud/`, separando configuração, API, domínio, persistência, fontes e worker. Instalação via `pyproject.toml` e lockfile do uv.
- API e worker usam a mesma imagem. Worker inicial valida configuração e encerra; ainda não processa jobs nem precisa ficar em loop vazio.
- `AGROJUD_ENV=demo|real|test`; `DATABASE_URL`, `TEST_DATABASE_URL` e `DATAJUD_API_KEY`. Default demo. Real não faz chamada externa nesta SPEC.
- Projetos Compose distintos para demo/real, com volumes e nomes de banco separados. Teste usa banco descartável próprio; recusar URL de teste idêntica à operacional e banco sem sufixo de teste.
- `GET /api/v1/health/live`: 200 se processo responde. `ready`: 200 se banco acessível e revisão Alembic no head; caso contrário 503, sem expor DSN.
- Migração inicial sem tabelas de produto; Alembic estabelece a revisão. Migrações executadas por comando explícito, não concorrentemente no startup de cada serviço.
- API publicada em 127.0.0.1, porta configurável, default 8000; banco sem porta pública. Configuração de exemplo sem segredo.
- Comandos documentados para instalar, lint, testar, migrar, subir e parar. CI usa PostgreSQL real, sem acesso ao CNJ.

## Comportamento esperado
Instalação limpa permite migrar e obter readiness. Parar/iniciar preserva o volume. Banco indisponível não impede liveness, mas impede readiness.

## Decisões importantes
Persistência e endpoints síncronos. Nenhum login local. Fixture de comprovação de persistência cria tabela temporária de diagnóstico no banco de teste e remove-a ao final; não inserir dado artificial de produto.

## Critérios de aceitação
- AC1: checkout limpo sobe seguindo instruções, sem ferramenta global além das documentadas.
- AC2: migration e readiness distinguem banco vazio, atualizado e indisponível.
- AC3: dado de diagnóstico persiste após reinício do banco.
- AC4: testes recusam banco operacional e segredos não aparecem em logs/config versionada.

## Testes necessários
AC1: build e smoke Compose. AC2: testes HTTP com estados de banco e migration. AC3: inserir/reiniciar/consultar no banco isolado. AC4: testes de validação da configuração e captura de logs.

## Erros e edge cases
Porta ocupada: falha explícita e override documentado. Configuração inválida: fail-fast. Não recriar volumes para corrigir migration. Testes com banco indisponível falham em vez de passar por skip silencioso.

## Fora do escopo
DTOs DataJud, domínios processuais, fila, frontend funcional, fixtures reais e coleta.

## Evidência e conclusão

Concluída em 08/10/2026. Versões verificadas contra as fontes oficiais em 08/10/2026: [Python 3.14.8](https://www.python.org/downloads/release/python-3148/) em manutenção estável, [PostgreSQL 18.6](https://www.postgresql.org/support/versioning/) suportado, e [uv 0.12.20](https://github.com/astral-sh/uv/releases/tag/0.12.20). O lockfile fixa as dependências diretas e transitivas; Dockerfiles fixam imagens Python e uv por versão e digest, e Compose fixa PostgreSQL 18.6.

Dependências diretas resolvidas em `backend/uv.lock`: Alembic 1.20.0, FastAPI 0.143.0, HTTPX 0.28.1, Psycopg 3.3.6, Pydantic 2.14.0, pydantic-settings 2.15.0, SQLAlchemy 2.1.4, Uvicorn 0.54.0, pytest 9.1.1, Ruff 0.16.10, mypy 1.20.2 e Hatchling 1.32.4. uv 0.12.20 gerencia o lockfile.

Critérios atendidos:

- **AC1 — PASSOU:** `docker compose config --quiet` para demo, real e teste; `./scripts/smoke-compose.sh demo` construiu a imagem, subiu PostgreSQL e API, aplicou a migration, validou liveness/readiness e executou o worker. A porta 8000 estava ocupada por outro serviço, então o smoke usou `API_PORT=18002`, conforme o override documentado.
- **AC2 — PASSOU:** testes HTTP/PostgreSQL cobrem readiness 503 antes da migration sem escrita, readiness 200 no head, liveness 200 com banco indisponível e readiness 503 sem DSN na resposta. A migration de banco vazio cria somente `alembic_version`.
- **AC3 — PASSOU:** `./scripts/verify-persistence.sh` escreveu um registro de diagnóstico, parou e iniciou o banco isolado, leu o mesmo registro e removeu a tabela diagnóstica. Nenhum volume foi recriado ou removido.
- **AC4 — PASSOU:** testes rejeitam URL de teste idêntica à operacional e nome de banco sem `_test`; teste de captura confirma que o worker não imprime DSNs nem API key. Arquivos locais `.env.*` são ignorados pelo Git e os exemplos não contêm credenciais operacionais.

Validação local final em Docker com Python 3.14.8 e PostgreSQL 18.6:

```text
docker compose --project-name agrojud-test --env-file .env.test -f compose.test.yaml --profile test run --rm --build tests sh -lc 'uv run --no-sync ruff check src tests && uv run --no-sync ruff format --check src tests && uv run --no-sync mypy src && uv run --no-sync pytest'
All checks passed; 20 files already formatted; mypy: 16 source files; 10 tests passed.
/tmp/agrojud-uv/uv lock --project backend --check --python /usr/bin/python3
Resolved 41 packages; lockfile is current.
git diff --check
Passed.
```

O workflow `.github/workflows/ci.yml` usa PostgreSQL real e não consulta o CNJ; a mesma sequência de lint, typecheck e testes foi executada localmente. Não há versão de migration anterior para validar nesta fundação. DONE conclui apenas a SPEC-001 e não representa integração DataJud, validação de sucessoras ou prontidão operacional.

