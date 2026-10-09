# SPEC-008 — Paginação e checkpoints

Status: DONE

Milestone/Spike: M4

## Dependências

[SPEC-007](SPEC-007-jobs-leases-e-posse.md)

Referências: [Plano](../IMPLEMENTATION_PLAN.md) e [PRD](../PRD.md). Executar somente esta unidade; não implementar suas sucessoras.

## Objetivo
Executar ingestão multipágina com checkpoint e dados confirmados atomicamente.

## Contexto
M4 conecta fonte, persistência e posse. A autorização de gravação vem da SPEC-007, não da resposta HTTP.

## Escopo
Handler discovery/refresh_number, limites e continuação interna. Retries e comandos de retomada entram na SPEC-009.

## Requisitos técnicos e contratos
- Ciclo: reservar → ler checkpoint → HTTP sem transação → validar envelope → transação de página → verificar posse/revisão → persistir dados/quarentena/contadores/checkpoint → commit.
- Página padrão 100, orçamento inicial 2.000 hits de origem confirmados, inclusive rejeitados; tamanho da próxima requisição não excede saldo.
- Consultas, sort e intervalo resolvido congelados na coleta. Cursor é integralmente o sort retornado, sem deduzi-lo de data/id.
- Validar avanço conforme sort aprovado; cursor repetido, ciclo para cursor anterior ou sort ausente gera CONTRACT e não avança página.
- Chave de página combina coleta e revisão anterior do checkpoint. Idempotência de replay não depende do horário de execução.
- Contadores confirmados de hits = válidos + rejeitados; novos/atualizados/inalterados referem-se a representações válidas. Tentativas HTTP contam separadamente.
- Página vazia confirma exaustão; página curta isolada não será usada como prova universal de exaustão. Se orçamento esgota antes dessa prova, terminar partial com reason=limit.
- Exaustão com rejeições termina partial/rejections. Falha depois de páginas confirmadas termina failed, com has_persisted_data e cobertura parcial.
- Continuação só aceita partial/limit com cursor utilizável e adiciona até 2.000 ao orçamento, preservando revisão e contadores. Rejeições históricas continuam explícitas.
- Modo real exige capacidades aprovadas de SPEC-003; modo sintético exercita toda a transação.
- Atualização por número deve paginar todas as representações encontradas; nunca assumir hit único.

## Comportamento esperado
Crash antes do commit repete a página. Crash após commit parte da próxima revisão. Falha de persistência mantém checkpoint anterior mesmo que HTTP tenha funcionado.

## Decisões importantes
Não usar total remoto para decidir conclusão nem declarar snapshot consistente. Mensagem completed será “consulta encerrada”; ausência na consulta não exclui dados anteriores.

## Critérios de aceitação
- AC1: execução contínua e replay produzem os mesmos efeitos locais.
- AC2: falha em qualquer gravação reverte página e checkpoint.
- AC3: limite resulta parcial, e continuação preserva critérios.
- AC4: cursor inválido/repetido não entra em loop.
- AC5: exaustão, rejeição e falha com dados têm resultados distintos.

## Testes necessários
Fixtures de 0/1/2/mais páginas, limite exato e página curta; falhas antes/depois do commit; perda de posse durante HTTP; erro ao gravar quarentena; cursor cíclico e múltiplas capas por número. PostgreSQL obrigatório para AC1/AC2.

## Erros e edge cases
Mutação da fonte pode deslocar registros; documentar limite e depender de revarreduras, não “corrigir” cursor. Último hit inválido pode fornecer sort válido; aceitar só se envelope e cursor íntegros. Falta de sort final aborta.

## Fora do escopo
Backoff, scheduler, UI, aprovação remota sem evidência e snapshot/PIT.

## Evidência e conclusão
Concluída localmente em 09/10/2026. `JobService.commit_page` grava ingestão, quarentena, contadores, evento e avanço CAS do checkpoint na mesma transação, sob posse válida. A requisição HTTP e a validação do envelope ocorrem fora da transação. A chave determinística da página combina a coleta com a revisão anterior do checkpoint. Consultas e sort ficam congelados; o cursor guarda integralmente o sort retornado. Página curta não prova exaustão: somente página vazia encerra a consulta. Hits rejeitados contam para o orçamento e são confirmados com a quarentena. Nenhuma migration foi necessária.

O worker registra handlers locais para `discovery` e `refresh_number`. Busca por número pagina todas as representações encontradas. O contrato interno de continuação valida `partial/limit`, cursor e revisão e permite acrescentar no máximo 2.000 hits sem trocar cursor, revisão ou contadores. Comando e ciclo operacional de retomada continuam na SPEC-009.

| Critério | Resultado | Evidência |
| --- | --- | --- |
| AC1 — execução contínua e replay têm os mesmos efeitos locais | PASSOU | Teste PostgreSQL compara execução contínua com replay após commit e confirma ausência de observação duplicada. |
| AC2 — falha de gravação reverte página e checkpoint | PASSOU | Falha injetada ao gravar quarentena; teste confirma rollback dos dados e da revisão e repetição com a mesma chave determinística. |
| AC3 — limite parcial e continuação preservam critérios | PASSOU | Testes de orçamento exato e saldo por requisição; continuação valida limite, elegibilidade, cursor/revisão e preservação de contadores. |
| AC4 — cursor inválido/repetido não cria loop | PASSOU | Testes para cursor repetido e sort ausente; o segundo HTTP não é enviado quando o cursor não avança. |
| AC5 — exaustão, rejeição e falha com dados têm resultados distintos | PASSOU | Testes para página vazia, rejeições e falha HTTP após página persistida, com cobertura parcial e `has_persisted_data`. |

Validações executadas em PostgreSQL 18.6 isolado (`agrojud-spec008-validation`, porta 55508; sem dados operacionais): Ruff check, Ruff format, mypy (`38 source files`) e suíte completa (`153 passed`). Também passaram as configurações Compose demo/real/test, build de produção das imagens API/worker, `agrojud-worker --check` e importação runtime do handler de coleta. A revisão final do diff não encontrou erros de whitespace (`git diff --check`).

Os testes usam HTTP simulado. Não houve consulta real ao DataJud: S2 permanece inconclusivo e o handler mantém a fonte real bloqueada por `DATAJUD_PAGINATION_APPROVED = False`. Essa evidência local não aprova paginação real nem encerra M4/S4, cujos retries e comandos de recuperação pertencem à SPEC-009.

