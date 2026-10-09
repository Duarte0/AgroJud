# SPEC-013 — Triagem e histórico humano

Status: DONE

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
- [x] AC1: todas as transições válidas preservam histórico.
- [x] AC2: confirmação sem justificativa é rejeitada.
- [x] AC3: duas abas não sobrescrevem edição mais nova.
- [x] AC4: recoleta e GET não alteram triagem.
- [x] AC5: UI e filtros refletem os defaults dos processos ainda não revisados.

## Testes necessários
Integração transacional de update/history, 409 e rollback; 422 de nota; recoleta após revisão; filtros de registros ausentes. Playwright: revisar, reverter, consultar histórico e simular edição desatualizada.

## Erros e edge cases
Processo inexistente: 404. Payload parcialmente inválido não atualiza campos válidos isoladamente. Nota renderizada com escape; nenhuma decisão se deduz do nome do preset.

## Fora do escopo
Usuários, atribuição de responsável, triagem em lote, watchlists e pontuação jurídica.

## Evidência e conclusão

Implementação local concluída em 09/10/2026. A migration incremental `20261009_0006` cria estado humano separado dos dados importados e histórico protegido contra UPDATE/DELETE. A atualização valida a versão esperada, persiste estado e histórico na mesma transação e deixa GET/defaults como projeções sem escrita. A lista filtra estado persistido ou projetado, incluindo processos sem triagem salva. UI, histórico paginado e contratos OpenAPI estão integrados; o conflito conserva o rascunho até o usuário recarregar e reaplicar.

Validações executadas:

- `docker compose --project-name agrojud-test --env-file .env.test -f compose.test.yaml --profile test run --build --rm tests uv run --no-sync pytest`: 222 testes passaram; inclui migration em banco vazio, upgrade de `20261009_0005` preservando processo existente, transações de triagem, filtros, defaults e recoleta. Um aviso conhecido de depreciação Starlette/HTTPX permanece.
- Ruff (`check` e `format --check`) e mypy passaram no backend.
- Frontend: `npm run lint`, `npm run typecheck`, `npm test` (42 testes) e `npm run build` passaram; `npm run openapi:check` confirmou schema e tipos atualizados.
- `./scripts/e2e.sh`: 9 cenários Playwright Chromium passaram, incluindo revisão, reversão, histórico, concorrência desatualizada e preservação de rascunho.
- `./scripts/verify-persistence.sh`, configurações Compose demo/real/test/E2E, build da imagem API/worker e `agrojud-worker --check` passaram.

Os testes usam PostgreSQL isolado e dados/HTTP sintéticos. Isso comprova a triagem local, não valida consultas ou resultados reais do DataJud/TJGO e não encerra gates externos de S1/S2/S5. A entrega 2 de M7 (regras, sinais e reprocessamento) continua no escopo da SPEC-014.

