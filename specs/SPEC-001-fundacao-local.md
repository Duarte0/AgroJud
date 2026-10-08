# SPEC-001 — Fundação local

Status: READY

Milestone/Spike: M0

## Dependências

Nenhuma.

Referências: [Plano](../IMPLEMENTATION_PLAN.md) e [PRD](../PRD.md). Executar somente esta unidade; não implementar suas sucessoras.

## Objetivo
Entregar uma base local reproduzível para executar API, worker e testes com PostgreSQL real.

## Contexto
M0 antecede qualquer coleta. O repositório ainda é documental; não criar tabelas de domínio para simular progresso.

## Escopo
Pacote backend, configuração, imagem compartilhada, Compose, migrations, endpoints de saúde e CI. Reservar frontend com documentação; aplicação React pertence à SPEC-012.

## Requisitos técnicos e contratos
- Usar Python 3.12, PostgreSQL 17, FastAPI, Pydantic, SQLAlchemy 2, Psycopg 3, Alembic, HTTPX, pytest e Ruff. Fixar versões resolvidas e imagem por versão/digest na implementação; não usar tags flutuantes.
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
Registrar comandos, versões resolvidas, resultados de CI local e persistência. DONE exige AC1–AC4; não indica integração DataJud.

