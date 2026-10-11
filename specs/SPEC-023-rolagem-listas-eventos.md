# SPEC-023 — Rolagem vertical nas listas de eventos

Status: DONE

Data: 11/10/2026

Dependências: [SPEC-012 — Frontend de coleta e consulta](SPEC-012-frontend-de-coleta-e-consulta.md), [SPEC-021 — Redesign do frontend](SPEC-021-redesign-frontend.md)

## Objetivo

Manter listas de eventos compridas dentro de uma área com rolagem vertical própria, preservando o espaço das ações, alertas, filtros e paginação que pertencem à tela.

## Decisões

- Usar o componente compartilhado `ScrollArea`, baseado em `overflow-y-auto`, limitado a `min(70vh, 48rem)` e com contenção da rolagem.
- Nomear e incluir cada região na ordem de tabulação; o foco recebe contorno visível.
- Aplicar o componente ao corpo da lista. Cabeçalhos, alertas, ações, filtros e paginação ficam fora do contêiner.
- Preservar a navegação para a evidência exata de uma movimentação; a movimentação continua recebendo foco quando aberta pela URL.
- Não alterar API, OpenAPI, banco ou conteúdo dos eventos.

## Escopo

- Linha do tempo de movimentações no detalhe do processo.
- Lista de sinais vigentes e históricos do processo.
- Histórico de triagem e histórico auditável de acompanhamento.
- Lista de novidades.
- Eventos recentes no detalhe da coleta.

## Critérios de aceite

1. Cada área usa uma região nomeada, acessível por teclado e com rolagem vertical limitada a `min(70vh, 48rem)`.
2. A rolagem é interna à lista; alertas, ações, filtros e paginação permanecem fora dela.
3. A evidência do sinal mantém a URL, o marcador e o foco na movimentação correspondente.
4. As larguras de 390, 640, 768, 1024 e 1440 px não produzem rolagem horizontal na página ou na lista.
5. Testes de frontend, lint, typecheck, build e Playwright passam com dados sintéticos.
6. A atualização do runtime real publica somente o serviço frontend e mantém API, worker, banco e volumes existentes.

## Exclusões e limites

- Listas que não são histórico ou evento, como regras e tentativas em tabela, não recebem altura fixa.
- Evidência de Playwright usa o ambiente demo isolado e não valida a fonte DataJud.
- A implantação do frontend não altera a imagem ou o processo da API/worker.

## Evidências

- `npm test -- --run` — 11 arquivos e 61 testes aprovados.
- `npm run lint`, `npm run typecheck`, `npm run build` e `git diff --check` — aprovados.
- Playwright, `E2E_COMPOSE_PROJECT=agrojud-scroll-e2e E2E_API_PORT=18787 E2E_FRONTEND_PORT=18788 E2E_EVIDENCE_DIR=/tmp/agrojud-scroll-e2e-evidence ./scripts/e2e.sh e2e/signals.spec.ts --reporter=list --output=/tmp/agrojud-scroll-e2e-results` — 1 cenário aprovado; inclui overflow, rolagem com mouse, foco da evidência e cinco larguras. Captura: `/tmp/agrojud-scroll-e2e-results/signals-sinal-reprocessado-4bb38-corrência-exata-na-timeline-chromium/movement-scroll.png`.
- `graphify update .` — grafo atualizado; o updater sinalizou que nomes de comunidades salvos ficaram desatualizados após mudanças no agrupamento.
- Container real: imagem construída a partir do frontend do commit (`git archive HEAD frontend` e `docker build --tag agrojud-frontend:local`) e publicada com `docker compose --project-name agrojud-real --env-file .env.real -f compose.yaml up -d --no-deps --wait frontend`. Frontend saudável em `http://127.0.0.1:5174/`, HTTP 200, bundle contém o rótulo da lista e não contém a edição de buscas que estava pendente no workspace. API (`d8c3fbe68260`), banco (`ebeb65ed9de9`) e worker (`04b19fcc5c8f`) mantiveram os mesmos containers; nenhum volume foi recriado.
- A execução Playwright usou Playwright Chromium do projeto porque o browser MCP não estava disponível nesta sessão.
