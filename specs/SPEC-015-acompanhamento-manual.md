# SPEC-015 — Acompanhamento manual

Status: BLOCKED_DEPENDENCY

Milestone/Spike: M8

## Dependências

[SPEC-014](SPEC-014-sinais-e-reprocessamento-local.md)

Referências: [Plano](../IMPLEMENTATION_PLAN.md) e [PRD](../PRD.md). Executar somente esta unidade; não implementar suas sucessoras.

## Objetivo
Selecionar processos conhecidos e atualizar seus dados sob demanda por número CNJ.

## Contexto
M8 começa pelo acompanhamento manual, antes de baseline, novidades e agendamento.

## Escopo
Persistência da lista única, endpoints, UI e jobs de atualização por número usando a infraestrutura existente.

## Requisitos técnicos e contratos
- Watch entry único por processo: active, included_at, removed_at e histórico de mudanças. Remover desativa, não apaga processo.
- PUT /processes/{id}/watch idempotente; DELETE no mesmo recurso desativa. GET /watchlist paginado.
- POST /processes/{id}/refresh retorna 202 para processo acompanhado ativo; kind refresh_number, tribunal TJGO e CNJ, sem restrição por ajuizamento.
- Atualização usa o mesmo handler paginado, transações e limites de SPEC-008/009. Havendo várias capas, persistir todas.
- Deduplicação por tipo/fonte/tribunal/CNJ; clique repetido retorna job ativo. Operações com alvos diferentes continuam independentes.
- Registrar resultado da consulta por número: encontrado, ausente na consulta, parcial ou falha, com horário; não persistir rótulo jurídico “inexistente”.
- UI: lista acompanhada, botão no detalhe e ação atualizar; remover acompanhamento não altera relevância nem vínculo rural.
- Ao remover, jobs já iniciados podem terminar e persistir dados; não criar agendamento futuro e não confundir remoção com cancelamento do job.

## Comportamento esperado
Número antigo pode ser atualizado mesmo que fora da janela de descoberta. Vazio não remove histórico. Ainda não há caixa de novidades nesta unidade.

## Decisões importantes
Somente processos locais podem ser acompanhados; entrada arbitrária de número fora da base não é adicionada nesta SPEC. Histórico de inclusão/remoção será usado em SPEC-016.

## Critérios de aceitação
- AC1: incluir/remover/reincluir é idempotente e auditável.
- AC2: refresh ignora janela de ajuizamento e preserva múltiplas capas.
- AC3: clique repetido não cria jobs equivalentes.
- AC4: vazio/falha mantém dados e expõe resultado distinto.
- AC5: triagem não muda ao acompanhar/remover.

## Testes necessários
Integração de API/lista, duas inclusões concorrentes, refresh de processo antigo e multicapa, consulta vazia e 429; Playwright de inclusão, atualização e remoção.

## Erros e edge cases
Processo inexistente: 404. Refresh sem acompanhamento ativo: 409. Remover enquanto job executa não apaga resultado já confirmado.

## Fora do escopo
Múltiplas watchlists, novidades, agenda, inserção direta de CNJ e notificações externas.

## Evidência e conclusão
Demonstrar processo antigo acompanhado e atualizado com suas capas; histórico permanece após remoção.

