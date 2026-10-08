# SPEC-006 — Movimentos e quarentena

Status: BLOCKED_DEPENDENCY

Milestone/Spike: M2

## Dependências

[SPEC-004](SPEC-004-identidade-e-reconciliacao.md), [SPEC-005](SPEC-005-persistencia-de-capas-e-payloads.md)

Referências: [Plano](../IMPLEMENTATION_PLAN.md) e [PRD](../PRD.md). Executar somente esta unidade; não implementar suas sucessoras.

## Objetivo
Completar ingestão local com movimentos e quarentena, preservando conteúdo inválido sem comprometer a atomicidade.

## Contexto
M2 usa a política aprovada pela SPEC-004 e o serviço transacional da SPEC-005.

## Escopo
Migrations e ingestão de ocorrências, associação às versões, diagnóstico de normalização e reprocessamento local de rejeições.

## Requisitos técnicos e contratos
- Ocorrência única por representação, versão do algoritmo, fingerprint e ordinal de multiplicidade; ligação versão_payload/ocorrência guarda presença naquele snapshot.
- Preservar first_observed_at, data do movimento, bruto do movimento e resultado de comparação. Não apagar ocorrências ausentes no snapshot seguinte.
- Normalização de uma representação é completa apenas se a lista de movimentos estiver presente, válida e todos os elementos exigidos forem processados. Lista vazia explícita é válida; ausente é incompleta.
- Quarentena: coleta, página, ordinal do hit, caminho do erro, bruto, código de validação, normalizador, horário e situação pending/resolved; unicidade da rejeição por localização e diagnóstico.
- Se capa é válida e movimento não, persistir capa/bruto e rejeição localizada, marcar normalização incompleta. Não declarar baseline completo.
- Se identidade da capa é inválida, preservar hit em quarentena sem criar representação presumida.
- Serviço processa registros válidos e rejeições na mesma transação externa. Falha em gravar quarentena aborta a página.
- Reprocessamento por comando local com IDs explícitos, normalizador versionado e transação por hit; reaproveitar identidades e versões. Resolução acrescenta histórico, não apaga rejeição original.
- Reprocessamento não altera o resultado histórico da coleta e não avança checkpoint remoto. Contagens operacionais futuras podem informar rejeições resolvidas separadamente.

## Comportamento esperado
Rejeição de um movimento fica visível sem perda do payload. Replay não repete quarentena. Correção do normalizador permite resolver itens sem consultar o DataJud.

## Decisões importantes
Quarentena é falha de dados/contrato identificável; erros SQL e bugs inesperados abortam, não são mascarados como rejeição de registro.

## Critérios de aceitação
- AC1: política de multiplicidade/reordenação da SPEC-004 preservada no banco.
- AC2: rejeições e dados válidos confirmam ou revertem juntos.
- AC3: ausência de movimentos não equivale a captura histórica completa.
- AC4: replay e reprocessamento não duplicam ocorrências/rejeições.
- AC5: resolução mantém auditoria e resultado original da coleta.

## Testes necessários
AC1: duas capas, ocorrências iguais e arrays permutados. AC2: falha injetada na gravação de quarentena. AC3: ausente/null/vazio/inválido. AC4/AC5: repetir reprocessamento com normalizador corrigido.

## Erros e edge cases
Data ambígua preservada sem ordenação cronológica inventada. Campo extra permanece no bruto. Remoção no snapshot não exclui histórico. Reprocessar ID inexistente gera erro claro e não varre toda a quarentena.

## Fora do escopo
Fila de reprocessamento, sinais jurídicos, baseline, API de quarentena e limpeza automática.

## Evidência e conclusão
Relatório dos cenários de persistência e rollback, com normalizador utilizado. Não aprova sozinho a identidade real do DataJud.

