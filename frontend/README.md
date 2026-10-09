# Frontend — AgroJud Radar

Aplicação React da SPEC-012: radar de coleta, lista/detalhe de coletas e lista/detalhe de processos com timeline. Consome exclusivamente o contrato OpenAPI da SPEC-011; não há triagem, notas, dashboard ou gráficos nesta etapa.

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

- `/` redireciona para `/radar` enquanto não existir visão geral.
- `/radar`: presets com versão, vínculo rural e disponibilidade; itens indisponíveis ficam desabilitados com o motivo. Janela padrão de 12 meses resolvida pelo servidor, ou janela editável; limite de 1 a 2.000 registros.
- `/jobs` e `/jobs/:id`: estado persistido, progresso confirmado, tentativas, rejeições, cobertura, erros e comandos (cancelar, retomar, continuar, reiniciar varredura). A porcentagem só aparece quando o total remoto é exato.
- `/processes` e `/processes/:id`: filtros e página na URL; capas por origem e timeline com data do evento, atualização da fonte e observação local. Datas ambíguas são sinalizadas e ausentes aparecem como “Não informado”.

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

O schema [openapi.json](openapi.json) e os tipos [src/api-schema.ts](src/api-schema.ts) são gerados a partir da aplicação FastAPI em modo `demo`, sem banco ou DataJud (requer uv 0.12.20 e CPython 3.14.8):

```sh
npm run openapi:generate
npm run openapi:check
```
