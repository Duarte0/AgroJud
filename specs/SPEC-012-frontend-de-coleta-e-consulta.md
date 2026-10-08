# SPEC-012 — Frontend de coleta e consulta

Status: BLOCKED_DEPENDENCY

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
- AC1: iniciar coleta, acompanhar e abrir processo pela interface.
- AC2: reload/link direto mantém contexto e filtros.
- AC3: vazio, falha, parcial e carregamento são distinguíveis.
- AC4: polling encerra em estado terminal e não duplica comandos.
- AC5: teclado, largura reduzida, build e tipos passam.

## Testes necessários
Playwright com API/banco demo: fluxo principal, cancelamento, falha simulada, resultado parcial e reload. Testar requisições de polling e ausência de envio duplicado; verificação visual de tabelas e timeline.

## Erros e edge cases
Clique duplo desabilita envio até resposta e backend permanece idempotente. Job removido/inexistente mostra 404. Data ausente aparece “Não informado”, não data atual.

## Fora do escopo
Triagem, notas, sinais interativos, autenticação, SSR, notificações e gráficos.

## Evidência e conclusão
Registrar fluxo de navegador, screenshot dos estados relevantes, typecheck e build. DONE demonstra UI do núcleo, não o MVP jurídico completo.

