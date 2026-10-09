# SPEC-016 — Referência histórica e novidades

Status: DONE

Milestone/Spike: M8

## Dependências

[SPEC-015](SPEC-015-acompanhamento-manual.md)

Referências: [Plano](../IMPLEMENTATION_PLAN.md) e [PRD](../PRD.md). Executar somente esta unidade; não implementar suas sucessoras.

## Objetivo
Distinguir histórico de referência, conteúdo recém-observado e alterações ambíguas nos acompanhados.

## Contexto
M8 depende de snapshots normalizados completos. Uma resposta parcial não pode criar uma baseline artificial.

## Escopo
Baseline por representação e ciclo de acompanhamento, novidades e revisão via API/UI.

## Requisitos técnicos e contratos
- Baseline possui watch_cycle, representação, versão completa e momento de estabelecimento. Completude significa lista de movimentos presente e normalizada sem rejeição; não garante completude jurídica da fonte.
- Na inclusão, versões locais completas compõem baseline; representação incompleta fica pending. Na primeira versão completa posterior, incorporar o histórico sem gerar novidades de cada movimento.
- Nova representação após baseline de outras gera uma novidade NEW_REPRESENTATION; seu histórico inicial não gera N novidades.
- Comparar snapshots subsequentes com todo o histórico conhecido da representação, usando SPEC-004: NEW_OBSERVATION para conteúdo antes desconhecido, ALTERATION_OBSERVED para alteração potencial, nada para retorno exato.
- Novidade preserva data do evento original, first_observed_at, categoria, evidência e situação pending|reviewed. Não chamar NEW_OBSERVATION de “novo ato jurídico”.
- Unique por processo, representação, identidade técnica/evidência e categoria; review permanece após replay e reprocessamento.
- Inserção de novidades é parte da transação de ingestão; rollback da página também reverte novidades. Normalização incompleta não publica diferenças daquele snapshot; próxima versão completa reavalia.
- GET /news com filtros processo/situação/categoria e paginação; PATCH /news/{id} permite pending/reviewed. Exibir baseline pendente no detalhe.
- Remoção desliga geração de novidades; mantém histórico e revisão. Reinclusão abre ciclo com baseline do histórico conhecido; intervalo desligado não vira backlog automático.
- Reprocessamento de quarentena pode completar baseline ou produzir alteração observada conforme histórico, sempre idempotente e com proveniência local.

## Comportamento esperado
Movimento antigo recebido depois aparece como recém-observado, com ambas as datas. Snapshot inicial parcial não provoca inundação na correção.

## Decisões importantes
Baseline é por representação, não uma bandeira global do CNJ. Uma representação válida pode ter baseline mesmo se outro hit da consulta ficou em quarentena; a cobertura da consulta continua parcial.

## Critérios de aceitação
- AC1: captura inicial, inclusive parcial seguida de completa, não gera falsos eventos históricos.
- AC2: nova representação gera uma novidade própria e não todo seu histórico.
- AC3: reordenação e reaparecimento não duplicam novidades.
- AC4: revisão sobrevive a replay, reprocessamento e reinclusão.
- AC5: rollback e perda de posse não deixam novidades órfãs.

## Testes necessários
Fixtures de snapshots incompletos/completos, múltiplas capas, nome alterado, movimento antigo, duplicatas 1→2→1→2; integração transacional; Playwright de revisar/reabrir, datas e baseline pendente.

## Erros e edge cases
Sem data interpretável, ordenar observação local e mostrar data desconhecida. Falha em uma representação não autoriza inferir ausência nas outras. Dados iguais com multiplicidade maior são conteúdo recém-observado, não inferência de ato distinto.

## Fora do escopo
Prazos, urgência, detecção semântica de atos, deduplicação entre graus e notificações externas.

## Evidência e conclusão

A migration `20261009_0009` cria ciclos de acompanhamento, baselines por representação e novidades com chaves técnicas, evidência local, datas separadas, proveniência e revisão. Ciclos ativos existentes são preservados no upgrade; snapshots completos estabelecem baseline com o corte no momento em que o snapshot foi processado, e representações sem snapshot completo permanecem pending. Inclusão, remoção, ingestão e reprocessamento atualizam essas estruturas sob as transações e locks locais correspondentes. A API oferece `GET /api/v1/news` com filtros/paginação e `PATCH /api/v1/news/{id}` para revisão; a interface `/news` expõe evidências e datas sem chamar observação de novo ato jurídico.

Critérios e evidência de teste:

- **AC1:** `test_partial_version_stays_pending_until_first_complete_snapshot_is_baseline` e `test_quarantine_reprocessing_can_establish_baseline_and_publish_local_alteration` comprovam que parcial não estabelece referência nem publica diferenças e que a primeira versão completa é incorporada sem inundação histórica.
- **AC2:** `test_new_representation_emits_one_event_and_its_incomplete_history_is_baselined` comprova uma novidade por nova representação e baseline do seu histórico inicial.
- **AC3:** `test_alterations_multiplicity_reordering_and_review_survive_replay_and_reinclude`, `test_duplicate_multiplicity_and_reordering_do_not_duplicate_news` e `test_removal_disables_news_and_reinclusion_does_not_create_gap_backlog` cobrem alteração, multiplicidade, reordenação, replay e intervalo sem acompanhamento.
- **AC4:** os testes de replay/reinclusão e reprocessamento de quarentena comprovam que a revisão permanece `reviewed` e que o reprocessamento não multiplica a novidade.
- **AC5:** `test_news_page_rollback_and_lost_lease_leave_no_orphan_rows` comprova rollback conjunto da página e das novidades e rejeição após perda de posse.
- `test_migration_backfills_active_watch_baselines_without_losing_process` valida upgrade de `20261009_0008` com representações preexistentes completas e pendentes; o runtime dos testes também migra banco isolado vazio até `20261009_0009`.

Validações executadas em ambiente isolado: `pytest` backend (**245 passed**, 1 aviso de depreciação Starlette/HTTPX), Ruff check e format, mypy (60 arquivos), frontend lint, typecheck, Vitest (**49 passed**), build e Playwright (**12 passed**, incluindo baseline pendente, revisão/reabertura, datas e responsividade). Comandos frontend: `npm run lint`, `npm run typecheck`, `npm test`, `npm run build` (em `frontend/`) e `./scripts/e2e.sh` (na raiz). No backend, esses checks rodaram pelo serviço `tests` de `compose.test.yaml`, com `uv run --no-sync pytest`, `ruff check src tests`, `ruff format --check src tests` e `mypy src`, montando `backend/src` e `backend/tests` atuais e usando `.env.test`/PostgreSQL isolado. O OpenAPI foi exportado com `python -m agrojud.api.export_openapi --stdout` no container, os tipos gerados com `npm exec -- openapi-typescript`, e ambos comparados byte a byte com `frontend/openapi.json` e `frontend/src/api-schema.ts`. `npm run openapi:check` no host não é executável porque `uv` não está instalado nele.

Toda a evidência usa PostgreSQL e fixtures/HTTP sintéticos locais. Baseline completa significa apenas que a lista de movimentos foi normalizada sem rejeição; não prova cobertura jurídica nem valida DataJud/TJGO, filtros, sort ou paginação reais. Esta unidade está concluída localmente; capacidades reais continuam sujeitas aos gates S1/S2/S5.

