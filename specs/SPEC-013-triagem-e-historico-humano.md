# SPEC-013 — Triagem e histórico humano

Status: BLOCKED_DEPENDENCY

Milestone/Spike: M7

## Dependências

[SPEC-012](SPEC-012-frontend-de-coleta-e-consulta.md)

Referências: [Plano](../IMPLEMENTATION_PLAN.md) e [PRD](../PRD.md). Executar somente esta unidade; não implementar suas sucessoras.

## Objetivo
Permitir revisão humana de relevância e vínculo rural, com histórico preservado durante importações.

## Contexto
M7 adiciona a primeira ação jurídica ao fluxo visual. Relevância e vínculo rural são dimensões independentes.

## Escopo
Migration, serviços, API, UI e testes de triagem e notas. Sem acompanhamento ou engine de regras.

## Requisitos técnicos e contratos
- Triagem por processo: decision pending|relevant|discarded, rural_link unconfirmed|confirmed, note textual, version inteira, updated_at.
- Estado inicial projetado como pending/unconfirmed sem gravar durante GET. Primeira mutação cria registro atomicamente.
- Histórico append-only com estado anterior/posterior, versão, data e origem manual; não inventar usuário em sistema sem login.
- PATCH /processes/{id}/triage recebe expected_version e campos alterados; versão inicial 0. Responder estado novo e versão incrementada. 409 para concorrência; atualização idêntica não acrescenta histórico.
- Confirmação rural exige nota não vazia após trim, máximo 5.000 caracteres. Reversão de decisão ou vínculo permitida e auditada.
- GET /processes/{id}/triage-history paginado. Acrescentar filtros decision e rural_link à lista.
- Caso sem triagem persistida participa do filtro pending/unconfirmed. Filtros e contagens não excluem processos recém-importados.
- UI mostra controles separados, nota e histórico; conflito pede recarregar dados sem descartar silenciosamente texto digitado.
- Importador e reprocessador não recebem permissão lógica para sobrescrever essas tabelas; API GET continua sem mutação.

## Comportamento esperado
Processo pode ser relevante com vínculo rural não confirmado. Marcar relevante não inicia acompanhamento. Recoletar não altera nota ou versão humana.

## Decisões importantes
Um operador ainda pode editar em duas abas; controle de versão é necessário mesmo sem contas. Notas são texto simples, sem HTML ativo.

## Critérios de aceitação
- AC1: todas as transições válidas preservam histórico.
- AC2: confirmação sem justificativa é rejeitada.
- AC3: duas abas não sobrescrevem edição mais nova.
- AC4: recoleta e GET não alteram triagem.
- AC5: UI e filtros refletem os defaults dos processos ainda não revisados.

## Testes necessários
Integração transacional de update/history, 409 e rollback; 422 de nota; recoleta após revisão; filtros de registros ausentes. Playwright: revisar, reverter, consultar histórico e simular edição desatualizada.

## Erros e edge cases
Processo inexistente: 404. Payload parcialmente inválido não atualiza campos válidos isoladamente. Nota renderizada com escape; nenhuma decisão se deduz do nome do preset.

## Fora do escopo
Usuários, atribuição de responsável, triagem em lote, watchlists e pontuação jurídica.

## Evidência e conclusão
Demonstrar triagem antes/depois de recoleta e conflito de abas; atualizar tipos OpenAPI e executar testes do fluxo existente.

