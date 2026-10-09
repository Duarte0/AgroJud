# SPEC-008 — Paginação e checkpoints

Status: READY

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
Apresentar contagens e checkpoints antes/depois de replay. Registrar validação local separada de capacidades reais.

