# SPEC-015 — Acompanhamento manual

Status: DONE

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

- A migration `20261009_0008` cria uma entrada única por processo, com estado ativo, datas de inclusão/remoção, índice da lista ativa e histórico append-only protegido por trigger. A migration passou em banco vazio e a partir de `20261009_0007`, preservando processos existentes.
- `PUT` e `DELETE /api/v1/processes/{id}/watch` são idempotentes e registram somente transições efetivas. `GET /api/v1/processes/{id}/watch` expõe estado e histórico; `GET /api/v1/watchlist` lista somente entradas ativas com paginação.
- `POST /api/v1/processes/{id}/refresh` enfileira `refresh_number` apenas para processo local acompanhado ativo. A criação genérica de refresh por `/api/v1/jobs` aplica o mesmo gate para não aceitar CNJs ausentes ou não acompanhados. Locks no processo serializam acompanhamento e enqueue; a chave existente do job coalesce o mesmo tipo/fonte/tribunal/CNJ e mantém alvos diferentes independentes.
- Cada job conserva a consulta, estado, cobertura e horário. A API projeta `found`, `absent_in_query`, `partial`, `failed`, `cancelled` ou `pending`; o texto e o estado persistido não chamam o processo de juridicamente inexistente. Uma consulta vazia ou falha não remove capas ou movimentos locais.
- A lista Acompanhados e o painel no detalhe permitem incluir, remover e atualizar. A remoção não cancela job enfileirado, não apaga o processo e não muda triagem ou vínculo rural.
- AC1 foi comprovado com inclusão/remoção/reinclusão idempotentes, duas inclusões concorrentes e histórico imutável. AC2 foi comprovado atualizando processo com representação ajuizada em 2001 sem filtro de data e preservando todas as capas retornadas; o teste de coleta por CNJ pagina três capas. AC3 foi comprovado com clique repetido reutilizando o job e alvos distintos gerando jobs distintos. AC4 foi comprovado para consulta vazia, falha, resposta 429 em retry e resultado parcial. AC5 foi comprovado comparando triagem antes/depois do acompanhamento e refresh.
- Backend: `pytest -q` passou com 237 testes em PostgreSQL isolado; Ruff check, formatação e mypy passaram. As migrations foram exercitadas em banco vazio e a partir de revisões anteriores suportadas.
- Frontend: lint, typecheck, 47 testes Vitest e build passaram. O schema OpenAPI e os tipos TypeScript gerados conferem byte a byte com a API.
- Playwright Chromium: `./scripts/e2e.sh` passou com 11 cenários na stack demo isolada. O cenário desta SPEC inclui, atualiza, remove, consulta o histórico e confirma o resultado persistido.

A entrega conclui apenas o acompanhamento manual local com fonte sintética. Não habilita a integração real DataJud/TJGO, baseline/novidades ou agendamento; SPEC-016 e SPEC-017 continuam fora desta unidade.

