# SPEC-017 — Buscas salvas e agendamento

Status: DONE

Milestone/Spike: M8

## Dependências

[SPEC-016](SPEC-016-referencia-historica-e-novidades.md)

Referências: [Plano](../IMPLEMENTATION_PLAN.md) e [PRD](../PRD.md). Executar somente esta unidade; não implementar suas sucessoras.

## Objetivo
Persistir buscas salvas e executar uma atualização diária por alvo, sem disparos duplicados ou backlog artificial.

## Contexto
M8 fecha automação sobre jobs, acompanhamento e baseline já demonstrados.

## Escopo
Busca salva, agenda, disparos e seleção de trabalho; controles mínimos de ativação na UI.

## Requisitos técnicos e contratos
- Saved search: nome, preset/versão, filtros, intervalo fixed ou rolling_12_months, enabled e timestamps. Criar a partir do radar; alterar cria nova versão.
- POST/GET/PATCH /saved-searches; permitir desativação, sem deletar coletas históricas. POST /saved-searches/{id}/run inicia execução manual pelos mesmos critérios.
- Agenda por busca habilitada ou watch entry ativo, às 06h America/Sao_Paulo; next_run_at armazenado em UTC.
- Loop do worker verifica agendas a cada 30s quando fora de HTTP/commit; atrasos por job ativo são permitidos e observáveis. Sem serviço cron separado.
- Em transação: bloquear agenda vencida, criar registro único de disparo alvo/data local, enfileirar ou associar job equivalente ativo e atualizar próximo horário.
- Após indisponibilidade de vários dias, um disparo de recuperação por alvo, marcado com intervalo perdido; não criar job para cada dia.
- Primeira habilitação agenda próxima ocorrência de 06h; executar imediatamente é comando manual explícito.
- Janela relativa resolve 12 meses de calendário na criação da coleta, inclusive ajuste de dia inexistente; fim exclusivo. Retomada reutiliza intervalo congelado.
- Só um job ativo por alvo agendado; se critérios mudaram enquanto anterior executa, manter disparo pendente para avaliação seguinte sem sobrescrever consulta em andamento.
- Escolha de jobs: alternar um refresh e um discovery quando ambas as filas têm elegíveis, começando por refresh; persistir última categoria para não reiniciar prioridade após restart. Reprocessamento local entra em FIFO com jobs da categoria não-refresh.
- Cooldown da fonte e retry_wait continuam respeitados. Rotação por página não é exigida; orçamento mantém execução limitada.
- Remover acompanhamento/desativar busca impede novo disparo; job já ativo só para mediante cancelamento explícito.

## Comportamento esperado
Reiniciar às 10h executa uma atualização vencida, não N dias de jobs. Clique manual concorrente ao scheduler reaproveita trabalho equivalente.

## Decisões importantes
Busca salva mantém versão do preset; atualização de catálogo não muda silenciosamente seu significado. Item desabilitado por validação não dispara HTTP e exibe motivo.

## Critérios de aceitação
- [x] AC1: transação de disparo/enqueue não deixa duplicata ou horário avançado sem job (`test_failure_after_enqueue_rolls_back_dispatch_job_and_next_time`, `test_two_scheduler_sessions_reserve_the_same_due_search_once`).
- [x] AC2: reinício agrega vencidos e calcula próximo horário corretamente (`test_restart_aggregates_missed_days_into_one_dispatch`, `test_watch_enable_waits_for_daily_time_and_schedule_reuses_manual_refresh`).
- [x] AC3: manual/agendado equivalentes convergem em um job (`test_manual_and_scheduled_saved_search_runs_reuse_one_active_job`, `test_watch_enable_waits_for_daily_time_and_schedule_reuses_manual_refresh`).
- [x] AC4: janela rolling muda em nova coleta e não em retomada (`test_rolling_window_uses_calendar_months_and_new_runs_freeze_their_dates`).
- [x] AC5: alternância não deixa descoberta indefinidamente atrás de refreshes (`test_worker_claims_refresh_and_discovery_in_alternating_persisted_turns`).

## Testes necessários
- [x] Relógio fixo: antes, exatamente às e depois das 06h; virada de ano e fevereiro bissexto (`test_next_daily_occurrence_respects_local_six_and_calendar_rollover`, `test_scheduled_instant_supports_february_leap_day`, `test_rolling_window_uses_calendar_months_and_new_runs_freeze_their_dates`).
- [x] Duas sessões reservando a mesma agenda (`test_two_scheduler_sessions_reserve_the_same_due_search_once`).
- [x] Falha entre enqueue e commit (`test_failure_after_enqueue_rolls_back_dispatch_job_and_next_time`).
- [x] Fila mista e alternância persistida (`test_worker_claims_refresh_and_discovery_in_alternating_persisted_turns`).
- [x] Edição/desativação com execução em andamento (`test_changed_criteria_wait_for_existing_target_job_then_dispatch_once`, `test_disabling_search_cancels_pending_dispatch_without_stopping_active_job`).
- [x] Playwright para salvar e habilitar busca (`frontend/e2e/saved-searches.spec.ts`).

## Erros e edge cases
Agendamento não roda enquanto máquina está desligada. Hora ambígua por mudança de timezone usa biblioteca zoneinfo e chave por data local, com uma execução por data. Banco indisponível não consome disparo.

## Fora do escopo
Cron configurável pelo usuário, múltiplos horários, scheduler distribuído e notificações.

## Evidência e conclusão
- Migration `20261009_0010` adiciona buscas com revisões imutáveis, dispatches idempotentes por alvo/data local, estado persistente da fila e próxima execução UTC para buscas e acompanhamentos ativos. A atualização agenda acompanhamentos já ativos para a próxima ocorrência, sem executar imediatamente.
- API `POST/GET/PATCH /api/v1/saved-searches` e `POST /api/v1/saved-searches/{id}/run` preserva versões anteriores, permite desativação sem apagar coletas e oferece execução manual explícita. O radar permite salvar filtros, ativar/desativar e executar; a lista de acompanhamentos mostra próxima execução e intervalos agregados.
- O worker avalia agendas a cada 30 segundos, fora de chamadas HTTP. Reserva, registro de dispatch, enqueue e avanço do próximo horário usam a mesma transação. Recuperação agrega dias vencidos por alvo; alteração de critérios durante job ativo mantém um dispatch pendente, sem substituir a consulta em curso. Cancelar o alvo não interrompe job ativo.
- As revisões registram versão e snapshot do catálogo. Janela rolling resolve 12 meses de calendário na criação da coleta e a retomada reutiliza o intervalo persistido. Em modo real, a disponibilidade informa o gate S1/S2 e não enfileira consulta DataJud.
- Validação local: `docker compose --env-file .env.test -f compose.yaml -f compose.test.yaml --profile test run --rm tests` aprovou 261 testes backend. Ruff (`ruff check src tests`, `ruff format --check src tests`) e mypy (`mypy src`, 66 módulos) passaram no container de desenvolvimento. No frontend, `npm run lint`, `npm run typecheck`, `npm test` (49 testes) e `npm run build` passaram. `./scripts/e2e.sh` aprovou 13 cenários; a exportação OpenAPI atual e os tipos gerados foram comparados aos artefatos versionados. A migration foi aplicada em PostgreSQL isolado durante testes e E2E.
- Os critérios AC1–AC5 estão cobertos pelos testes nomeados acima; o fluxo de salvar/ativar também foi verificado no navegador. A validação usa fonte sintética e não aprova filtros nem respostas reais do DataJud/TJGO. S1/S2/S5 permanecem gates externos; SPEC-018/019 continuam fora desta entrega.

