# SPEC-007 — Jobs, leases e posse

Status: BLOCKED_DEPENDENCY

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

## Critérios de aceitação
- AC1: disputa cria/reserva uma única execução equivalente.
- AC2: lease vencida é recuperada e token antigo não grava.
- AC3: heartbeat não mantém transação de ingestão aberta.
- AC4: cancelamento preserva páginas confirmadas e impede confirmações posteriores.
- AC5: eventos e tentativas sobrevivem ao reinício.

## Testes necessários
PostgreSQL com duas sessões/barreiras para AC1/AC2/AC4; relógio controlado ou lease curta de teste. AC3: handler bloqueado e heartbeat independente. AC5: reiniciar worker com job de diagnóstico.

## Erros e edge cases
Erro de banco durante heartbeat não gera posse presumida. Handler inesperadamente falho termina tentativa com erro sanitizado. Reserva vazia é worker ocioso, não sucesso de coleta.

## Fora do escopo
HTTP DataJud, backoff operacional, escalabilidade multiworker em produção e scheduler.

## Evidência e conclusão
Documentar tabela de transições, testes de concorrência e tokens invalidados. DONE ainda não representa coleta resiliente completa.

