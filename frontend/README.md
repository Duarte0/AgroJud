# Frontend — AgroJud Radar

Aplicação React do AgroJud Radar: radar de coleta, listas/detalhes de jobs e processos, acompanhamento, novidades e visão geral da base local. Consome exclusivamente o contrato OpenAPI; os tipos são gerados a partir da API.

## Stack

React 19, TypeScript 6.0, Vite 8, React Router 8, TanStack Query 5, Tailwind CSS 4 e componentes no padrão shadcn/ui (`src/components/ui`, primitivas Radix). O cliente HTTP é `openapi-fetch` tipado por [src/api-schema.ts](src/api-schema.ts); [src/api/types.ts](src/api/types.ts) contém apenas aliases dos tipos gerados. TypeScript fica em 6.0 porque o typescript-eslint 8.71 ainda não suporta a linha 7.

## Desenvolvimento

Use Node.js na versão de [.nvmrc](.nvmrc). Com a API demo em `127.0.0.1:8000`:

```sh
npm ci
npm run dev   # http://127.0.0.1:5173
```

Vite escuta somente em `127.0.0.1` e encaminha `/api` para `AGROJUD_API_URL` (padrão `http://127.0.0.1:8000`); o navegador fala apenas com a origem local. A API aceita CORS somente da origem configurada em `FRONTEND_ORIGIN` no backend (padrão `http://127.0.0.1:5173`, restrita a loopback com porta explícita).

## Rotas e comportamento

- `/`: indicadores descritivos da amostra persistida localmente, com filtros do processo, triagem, acompanhamento, novidades pendentes, sinais vigentes, temas e últimas coletas. Os cartões e temas abrem listas com o recorte correspondente; a tela distingue base vazia de falha de API e informa que a amostra não representa todo o TJGO.
- `/radar`: presets com versão, vínculo rural e disponibilidade; itens indisponíveis ficam desabilitados com o motivo. Janela padrão de 12 meses resolvida pelo servidor, ou janela editável; limite de 1 a 2.000 registros.
- `/jobs` e `/jobs/:id`: estado persistido, progresso confirmado, tentativas, rejeições, cobertura, erros e comandos (cancelar, retomar, continuar, reiniciar varredura). A porcentagem só aparece quando o total remoto é exato.
- `/processes` e `/processes/:id`: filtros e página na URL; capas por origem, timeline com data do evento, atualização da fonte e observação local, triagem, sinais e acompanhamento manual. A atualização por número só é aceita para processo local acompanhado e não usa a janela de descoberta.
- `/watchlist`: lista paginada de processos acompanhados, resultado/horário da última consulta, atualização manual e remoção. Remover não apaga o processo nem cancela job já iniciado.

Chaves de cache começam pelo ambiente (`demo`/`real`). Jobs `queued`, `running` e `retry_wait` são consultados a cada 3 s; o polling termina em estado terminal e volta após comando confirmado. Comandos não têm retry automático nem atualização otimista. Falha de leitura sem cache mostra erro com nova tentativa de leitura; com cache, os dados permanecem visíveis com aviso de desatualização.

## Validação

```sh
npm run lint
npm run typecheck
npm test            # Vitest + Testing Library
npm run build
npm run openapi:check
```

O fluxo de navegador usa uma stack demo isolada (projeto Compose `agrojud-e2e`, banco em tmpfs descartado ao final, API em `127.0.0.1:18765` e preview em `127.0.0.1:4173`):

```sh
cp .env.e2e.example .env.e2e
./scripts/e2e.sh
E2E_EVIDENCE_DIR="$PWD/docs/evidence/spec-012" ./scripts/e2e.sh   # também grava screenshots
```

Os testes Playwright param e reiniciam o worker para observar jobs em fila. Falhas de rede e um job `failed` são simulados por interceptação de requisições no navegador, porque a fonte demo não produz falhas; isso não valida a fonte real.

## Contrato de API

O schema [openapi.json](openapi.json) e os tipos [src/api-schema.ts](src/api-schema.ts) são gerados a partir da aplicação FastAPI em modo `demo`, sem banco ou DataJud (requer uv 0.12.22 e CPython 3.14.8):

```sh
npm run openapi:generate
npm run openapi:check
```
