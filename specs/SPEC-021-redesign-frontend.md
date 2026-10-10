# SPEC-021 — Redesign do frontend jurídico e de dados

Status: DONE

Milestone/Spike: Pós-M9 / UX

## Dependências

[SPEC-012](SPEC-012-frontend-de-coleta-e-consulta.md), [SPEC-013](SPEC-013-triagem-e-historico-humano.md), [SPEC-014](SPEC-014-sinais-e-reprocessamento-local.md), [SPEC-015](SPEC-015-acompanhamento-manual.md), [SPEC-016](SPEC-016-referencia-historica-e-novidades.md), [SPEC-017](SPEC-017-buscas-salvas-e-agendamento.md), [SPEC-018](SPEC-018-indicadores-da-base-local.md), [SPEC-019](SPEC-019-exportacao-csv.md), [SPEC-020](SPEC-020-operacao-e-aceite-integrado.md)

## Objetivo

Dar ao AgroJud Radar uma interface de trabalho profissional para pesquisa e revisão de contencioso rural, preservando os fluxos e contratos funcionais já entregues.

## Direção visual

Interface interna clara, sóbria e contemporânea. Verde profundo identifica ações primárias e navegação ativa; fundos neutros e painéis planos mantêm a leitura de dados; âmbar, azul e vermelho ficam reservados a estados semânticos. IBM Plex Sans atende leitura e formulário; IBM Plex Mono identifica CNJ, IDs e valores tabulares. Evitar estética de marketing, fundos decorativos, gradientes, sombras amplas e cartões repetidos sem função. Os tokens e padrões estão em [Sistema visual do frontend](../frontend/DESIGN_SYSTEM.md).

## Escopo

- Navegação de trabalho e coleta; adaptação da barra lateral para tablet e drawer acessível no celular.
- Hierarquia, densidade, cards, tabelas, formulários, filtros, badges, feedback e estados de página.
- Visão geral, lista de processos, Radar, detalhe de processo, acompanhados, novidades, lista de coletas e detalhe de coleta.
- Navegação por abas e preservação de rascunho, filtros, paginação, origem da navegação e links para evidência.
- Documentação do sistema visual e validação de teclado, foco, contraste, zoom e larguras móveis/tablet/desktop.

## Requisitos e limites

- Preservar URLs, filtros, ordenação do servidor, tamanho de página, filtros de exportação, payloads, endpoints e semântica dos dados.
- Reutilizar React Router, TanStack Query, componentes Radix existentes e tipos OpenAPI gerados; não criar uma segunda camada de dados.
- Separar estados de carregamento, dado vazio, erro sem cache, dado defasado e sucesso parcial.
- Distinguir datas do evento, da fonte e da observação local, conservar proveniência e multiplicidade e evitar inferência jurídica pela cor ou pelo texto visual.
- Pedir confirmação antes de ações destrutivas, manter rascunhos ao trocar abas e alertar antes de sair de triagem não salva.
- Manter acessibilidade por teclado, foco visível, nomes acessíveis, contraste legível, alvos adequados a toque e movimento reduzido.
- Não alterar API, worker, banco, modelo de dados, regras jurídicas ou habilitação de capacidades da fonte.

## Comportamento por área

- Visão geral: quatro métricas de leitura principal, indicadores auxiliares e temas com expansão, sem ampliar o escopo dos filtros existentes.
- Processos: três filtros frequentes e demais filtros recolhidos; critérios ativos removíveis; tabela compacta e registros rotulados em telas estreitas; exportação aplica todos os filtros ativos.
- Radar: abas para nova coleta e buscas salvas; formulário e resumo visíveis juntos em telas largas; detalhes de janela e orçamento preservados ao alternar abas.
- Detalhe: abas Movimentações, Capas por origem, Sinais e Histórico; painel persistente de triagem/acompanhamento; contexto da lista preservado; link de evidência ancora e foca o movimento correspondente.
- Acompanhados e novidades: controles, histórico e baselines hierarquizados; novidade exibe estado, datas, origem e evidência; exclusão exige confirmação.
- Coletas: cancelamento e retomada exigem confirmação; eventos técnicos ficam recolhidos inicialmente; preservar polling e acesso aos resultados.

## Critérios de aceitação

- AC1: todas as rotas existentes são alcançáveis pela navegação e mantêm estado/URL contextual ao navegar entre lista, detalhe e evidência.
- AC2: filtro avançado, filtros por URL, paginação e CSV mantêm os parâmetros e a semântica existentes; cada chip remove apenas o próprio critério.
- AC3: as quatro abas do processo exibem seus dados; rascunho permanece ao alternar abas e saída de triagem não salva exige confirmação.
- AC4: reprocessar, cancelar, retomar e remover usam confirmação quando a ação é destrutiva; mensagens de validação e falha aparecem junto ao contexto correspondente.
- AC5: vazios, carregamento, erro, stale e sucesso parcial continuam distinguíveis em todas as páginas.
- AC6: em 390, 640, 768, 1024 e 1440 px não há overflow horizontal; 640 px representa aproximadamente um desktop de 1280 px a 200% de zoom. Navegação móvel fecha após seleção, devolve foco ao fechar e aceita Escape; teclado permanece utilizável.
- AC7: frontend passa testes unitários, E2E, lint, typecheck e build; o contrato OpenAPI e o código gerado não mudam.
- AC8: tokens, tipografia, componentes e decisões de responsividade têm documentação versionada; fontes locais incluem sua licença.

## Validação

Suítes de frontend em banco demo isolado, E2E no Compose descartável com worker/API/frontend, largura/overflow em cinco viewports, cabeçalhos acessíveis de tabela, navegação por teclado e inspeção visual. Não usar a fonte DataJud como dependência de validação visual.

## Evidência e conclusão

Concluída em 10/10/2026. AC1–AC8 passaram. Não houve alteração em API, worker, banco, OpenAPI ou tipos gerados.

- `npm --prefix frontend run lint`, `typecheck`, `test` (56/56) e `build` passaram; o build produziu chunk inicial de 320,39 kB (100,92 kB gzip), sem aviso de chunk acima do limite.
- `UV_PROJECT_ENVIRONMENT=/tmp/agrojud-redesign-uv-env npm --prefix frontend run openapi:check` confirmou schema e tipos atualizados.
- `./scripts/e2e.sh` passou 20/20 no projeto Compose isolado `agrojud-redesign-verify-e2e`; as jornadas cobriram coleta, exportação, filtros, abas, triagem, cancelamento, retomada, estado stale/vazio/erro, teclado e foco.
- Após os últimos ajustes de rota e acessibilidade, `./scripts/e2e.sh e2e/redesign.spec.ts` passou 3/3 e `./scripts/e2e.sh e2e/states.spec.ts:153` passou 1/1. As páginas foram verificadas em 390, 640, 768, 1024 e 1440 px; 640 px aproxima desktop de 1280 px a zoom 200%. Cabeçalhos de tabela continuam no nome acessível em telas estreitas.
- A revisão visual confirmou quebra de nomes de órgão julgador extensos, sem overflow horizontal. Capturas temporárias estão em `/tmp/agrojud-redesign-evidence-final/`.
- Pares de texto principais foram calculados entre 5,61:1 e 7,91:1; borda de controle sobre cartão, 3,04:1.

Todas as validações funcionais usaram fixtures sintéticas em Compose demo isolado. Nenhuma chamada ao DataJud foi necessária para esta SPEC.
