# SPEC-016 — Referência histórica e novidades

Status: BLOCKED_DEPENDENCY

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
Demonstrar sequência de snapshots com contagens e revisão preservada; registrar explicitamente o limite entre completude técnica e jurídica.

