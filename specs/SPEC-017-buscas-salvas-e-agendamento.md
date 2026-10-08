# SPEC-017 — Buscas salvas e agendamento

Status: BLOCKED_DEPENDENCY

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
- AC1: transação de disparo/enqueue não deixa duplicata ou horário avançado sem job.
- AC2: reinício agrega vencidos e calcula próximo horário corretamente.
- AC3: manual/agendado equivalentes convergem em um job.
- AC4: janela rolling muda em nova coleta e não em retomada.
- AC5: alternância não deixa descoberta indefinidamente atrás de refreshes.

## Testes necessários
Relógio injetável: antes/depois das 06h, virada de mês/ano e fevereiro; duas sessões reservando agenda; falha entre enqueue e atualização; fila mista; edição/desativação durante execução; Playwright de salvar e habilitar busca.

## Erros e edge cases
Agendamento não roda enquanto máquina está desligada. Hora ambígua por mudança de timezone usa biblioteca zoneinfo e chave por data local, com uma execução por data. Banco indisponível não consome disparo.

## Fora do escopo
Cron configurável pelo usuário, múltiplos horários, scheduler distribuído e notificações.

## Evidência e conclusão
Demonstrar recuperação de dias vencidos e ausência de duplicação manual/agendada; registrar limites operacionais.

