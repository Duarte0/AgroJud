# SPEC-012 — Frontend de coleta e consulta

Status: DONE

Milestone/Spike: M6

## Dependências

[SPEC-011](SPEC-011-api-operacional-e-openapi.md)

Referências: [Plano](../IMPLEMENTATION_PLAN.md) e [PRD](../PRD.md). Executar somente esta unidade; não implementar suas sucessoras.

## Objetivo
Entregar o primeiro fluxo visual completo de coleta e consulta.

## Contexto
M6 utiliza exclusivamente o contrato da SPEC-011; não precisa de triagem ou indicadores para demonstrar o núcleo.

## Escopo
Aplicação React, navegação, radar, jobs, processos, detalhes, timeline e teste de navegador.

## Requisitos técnicos e contratos
- React/TypeScript/Vite, React Router, TanStack Query, Tailwind e shadcn/ui. Reutilizar tipos OpenAPI existentes, sem interfaces duplicadas à mão.
- Rotas /radar, /jobs, /jobs/:id, /processes e /processes/:id; root redireciona ao radar até existir visão geral.
- Filtros/página na URL; links diretos e reload funcionam. Estados operacionais em português.
- Query keys incluem filtros e ambiente. Invalidar listas/detalhe após comando; não mostrar job como iniciado antes da confirmação do backend.
- Polling de jobs queued/running/retry_wait a cada 3s; interromper em estado terminal e retomar após comando confirmado.
- Formulário mostra preset/versionamento, janela default editável e limite. Itens reais indisponíveis mostram motivo, sem botão habilitado.
- Detalhes de job: progresso confirmado, tentativas, rejeições, última atividade, cobertura e erros. Sem porcentagem se total remoto não for exato.
- Detalhes processuais preservam capas, origem e três datas: evento, atualização de fonte e observação local. Datas ambíguas indicadas como tal.
- Barra persistente indica demo/real. Servir somente localhost; dev proxy para API e CORS restrito à origem local configurada.
- Controles com rótulos acessíveis, teclado, foco visível; layout desktop e uso viável em largura de 390px. Aplicar skills frontend pertinentes na implementação, sem gerar redesign de todo o produto.

## Comportamento esperado
Erro de rede apresenta retry de leitura sem criar outra coleta. Dados antigos em cache ficam identificados durante falha de atualização; não desaparecem como lista vazia.

## Decisões importantes
Sem dashboard ou gráficos. Polling só informa estado persistido. Cancelar não promete rollback dos resultados confirmados.

## Critérios de aceitação
- [x] AC1: iniciar coleta, acompanhar e abrir processo pela interface.
- [x] AC2: reload/link direto mantém contexto e filtros.
- [x] AC3: vazio, falha, parcial e carregamento são distinguíveis.
- [x] AC4: polling encerra em estado terminal e não duplica comandos.
- [x] AC5: teclado, largura reduzida, build e tipos passam.

## Testes necessários
Playwright com API/banco demo: fluxo principal, cancelamento, falha simulada, resultado parcial e reload. Testar requisições de polling e ausência de envio duplicado; verificação visual de tabelas e timeline.

Validação local em 09/10/2026:

- Frontend: `npm run lint`, `npm run typecheck`, `npm test` (Vitest, 39 testes) e `npm run build` passaram. Os testes de componente cobrem duplo clique com um único POST, erro sem cache distinto de vazio, dados em cache sinalizados após falha de atualização, filtros/página lidos da URL, 404, ausência de porcentagem para total não exato, falha de job, data ambígua e data ausente como “Não informado”.
- Navegador: `./scripts/e2e.sh` executou 8 cenários Playwright (Chromium) em duas rodadas consecutivas, todas aprovadas. A stack `agrojud-e2e` usa API, worker e PostgreSQL 18.6 demo com banco em tmpfs, descartado ao final. Os cenários cobrem fluxo principal com duplo clique (1 POST), parada do polling após estado terminal (nenhum GET de detalhe em 7 s), cancelamento de job em fila e retomada com retorno do polling, parcial por limite com continuação, falha de rede com cache, falha 503 sem cache, vazio, 404, reload/link direto de filtros e páginas, teclado/foco visível e largura de 390 px sem rolagem horizontal da página.
- Backend: CORS restrito a `FRONTEND_ORIGIN` (somente origem loopback com porta). Em PostgreSQL isolado, Ruff, formatação, mypy e `pytest` passaram com 214 testes; houve somente o aviso de depreciação Starlette/HTTPX já conhecido. O schema OpenAPI exportado em CPython 3.14.8 e os tipos gerados coincidiram byte a byte com os versionados; `uv` não estava disponível no host para `npm run openapi:check`, executado pela CI.
- `docker compose config --quiet` passou para demo, real e o override E2E.

## Erros e edge cases
Clique duplo desabilita envio até resposta e backend permanece idempotente. Job removido/inexistente mostra 404. Data ausente aparece “Não informado”, não data atual.

## Fora do escopo
Triagem, notas, sinais interativos, autenticação, SSR, notificações e gráficos.

## Evidência e conclusão
Registrar fluxo de navegador, screenshot dos estados relevantes, typecheck e build. DONE demonstra UI do núcleo, não o MVP jurídico completo.

Screenshots da última rodada estão em [docs/evidence/spec-012](../docs/evidence/spec-012/): radar, job concluído, processos da coleta, processo com capa e timeline, cancelado, parcial, falha com cache, falha sem cache, vazio, falha simulada, 404 e as telas em 390 px. A inspeção visual conferiu tabelas, timeline com origem e três datas, e o layout reduzido.

Limites registrados:

- Dados vêm de fixtures sintéticas demo; isso não valida o TJGO nem altera S1/S2/S5. A fonte demo não produz falhas: o job `failed` e as falhas de rede/503 foram simulados por interceptação no navegador.
- A fixture demo traz datas com fuso; a sinalização de datas ambíguas e ausentes foi comprovada por teste de componente, não pelo fluxo de navegador.
- Os componentes shadcn/ui foram escritos no padrão do projeto (`components.json`, Radix e `class-variance-authority`), sem executar o CLI do shadcn.
- TypeScript fica em 6.0.3 porque typescript-eslint 8.71.1 suporta TypeScript abaixo de 6.1.
- A interface é servida apenas pelo Vite em localhost (dev/preview); a inclusão do frontend no Compose permanece em M9.

