# Contrato de API

Este diretório contém o schema OpenAPI e os tipos TypeScript gerados para a API local da SPEC-011. A aplicação React permanece no escopo da SPEC-012.

## Gerar e validar

Use Node.js na versão indicada em [.nvmrc](.nvmrc), uv 0.12.20 e CPython 3.14.8. O script exporta o schema diretamente da aplicação FastAPI em modo `demo`, sem conectar ao banco ou chamar o DataJud.

```sh
npm ci
npm run openapi:generate
npm run openapi:check
```

Os arquivos versionados são [openapi.json](openapi.json) e [src/api-schema.ts](src/api-schema.ts). A verificação da CI compara ambos com a geração atual.

## API disponível

Com a API local iniciada, a documentação interativa fica em `/api/v1/docs` e o schema em `/api/v1/openapi.json`. O contrato inclui jobs, processos, representações, movimentos, presets e informações do ambiente. Criações e comandos operacionais usam os endpoints `/api/v1/jobs`; consultas locais usam `/api/v1/processes`. Coletas reais permanecem desabilitadas enquanto as capacidades S1/S2 e as evidências pertinentes do catálogo não forem aprovadas.
