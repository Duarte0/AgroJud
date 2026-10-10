# SPEC-002 — Contratos e adaptadores de fonte

Status: DONE

Milestone/Spike: M1

## Dependências

[SPEC-001](SPEC-001-fundacao-local.md)

Referências: [Plano](../IMPLEMENTATION_PLAN.md) e [PRD](../PRD.md). Executar somente esta unidade; não implementar suas sucessoras.

## Objetivo
Definir uma fronteira de fonte testável que nunca confunda falha com ausência de resultados.

## Contexto
M1 prepara ingestão sem banco de domínio. A documentação referenciada no PRD informa o formato esperado, mas a compatibilidade real depende da SPEC-003.

## Escopo
Contratos Python, cliente HTTP de chamada única e fonte sintética determinística. Catálogo completo fica na SPEC-010.

## Requisitos técnicos e contratos
- Operações `fetch_page(query, cursor, page_size)` e `fetch_by_case_number(numero, cursor, page_size)` retornam `SourcePage` ou erro tipado; `build_query_by_case_number(numero)` constrói a consulta validada. Nenhuma faz retry internamente.
- Consulta imutável: tribunal TJGO, filtro CNJ exato ou filtros permitidos por classe, assunto, órgão julgador e intervalo de ajuizamento semiaberto, versão do preset e sort. Listas de códigos são OR dentro da dimensão; dimensões distintas são AND. Consulta sem filtro é rejeitada. Não há DSL livre.
- O intervalo é início inclusivo e fim exclusivo. Para evitar ativar um desempate ainda não aprovado, o contrato local permite somente `@timestamp` como sort; a ordenação real e a paginação continuam sob SPEC-003.
- Cursor é lista opaca de valores escalares devolvidos no sort. O adaptador transmite esses valores sem reconstruí-los; `cursor_final` corresponde ao sort do último hit e fica ausente numa página vazia.
- `SourcePage`: hits brutos, metadados de total/value/relation quando presentes, cursor final, indicação de lista vazia e horário de resposta.
- Separar validação do envelope (hits/lista/sort) de validação individual do payload. Um hit inválido permanece bruto para futura quarentena; envelope inválido gera CONTRACT.
- Hit preserva _id, _source e sort. Para normalizar capa, exigir _source objeto, numeroProcesso com 20 dígitos e tribunal TJGO. Identificador de origem deve ser texto não vazio e seguir a correspondência aprovada em SPEC-003; em fixtures usar _source.id igual a _id. Divergência real ainda não validada é rejeição, não fallback arbitrário entre os IDs.
- Erros: VALIDATION, AUTHENTICATION, AUTHORIZATION, RATE_LIMIT, NETWORK, SOURCE_UNAVAILABLE, CONTRACT; incluir status HTTP, retry_after e mensagem sanitizada quando disponíveis.
- HTTPX síncrono; POST somente ao endpoint público TJGO permitido. Credencial por configuração. Connect 5s, read configurável por `DATAJUD_READ_TIMEOUT_SECONDS` (padrão 60s; no ambiente real deve ser menor que `JOB_LEASE_SECONDS`), write 20s, pool 5s e redirects desativados.
- Preservar valores opcionais ausentes e campos extras no bruto. Não converter datas desconhecidas em data atual ou zero.
- Fonte sintética suporta conjunto fixo de páginas, cursores opacos, consulta vazia, mutação entre execuções e erros injetáveis.
- Isolamento da configuração impede utilizar fonte real em ambiente demo por fallback; a escolha é explícita.

### Implementação entregue

- `agrojud.sources.contracts` contém `SourceQuery`, `SourcePage`, `SourceHit`, `NormalizedSourceCover`, `SourceError`/`SourceErrorCode`, `SourceAdapter`, compilação allowlisted e normalização mínima da capa.
- `DataJudSourceAdapter` usa HTTPX síncrono, endpoint fixo `api_publica_tjgo/_search`, credencial `Settings.datajud_api_key`, connect 5 s, read configurável (padrão 60 s), write 20 s e pool 5 s, sem redirects e uma única tentativa. Consulta inválida é rejeitada antes do HTTP.
- Respostas 400/422 são VALIDATION; 401 AUTHENTICATION; 403 AUTHORIZATION; 429 RATE_LIMIT com `Retry-After`; 404 e 5xx SOURCE_UNAVAILABLE; timeout/falha de conexão NETWORK; JSON/envelope inesperado CONTRACT. As mensagens e logs não incluem corpo remoto, cabeçalho Authorization, chave nem exceção de transporte.
- A validação do envelope exige objeto `hits`, lista de hits e lista `sort` não vazia por hit, com quantidade de valores compatível com o sort da consulta. Não valida o conteúdo de `_source` na leitura da página: mantém hits inválidos e todo o bruto (incluindo listas e campos extras) disponíveis para quarentena posterior.
- A normalização de capa exige `_source` objeto, `numeroProcesso` texto com 20 dígitos, `tribunal == TJGO`, `_source.id` e `_id` textuais e não vazios, e rejeita divergência sem escolher um deles como fallback. O uso real dessa correspondência permanece condicionado à evidência da SPEC-003.
- `SyntheticSourceAdapter` recebe fixtures explícitas por consulta, permite páginas, cursores sort opacos, consulta vazia, erros injetáveis e substituição da fixture entre execuções. Consulta sem fixture é VALIDATION, não sucesso vazio.
- `build_source_adapter` exige escolha explícita de fonte e limita demo a sintético, real a DataJud e teste a transporte HTTP simulado para DataJud. Erro da fonte real nunca aciona a sintética.

## Comportamento esperado
Uma chamada corresponde a uma tentativa HTTP. Resposta 200 com hits vazio é sucesso vazio; HTML com 200 é CONTRACT; 429 carrega Retry-After para o worker futuro.

## Decisões importantes
Cliente real pode ser testado por transporte simulado antes da validação externa. Isso não habilita coleta real de produto. IDs e desempate remoto permanecem candidatos até a SPEC-003.

## Critérios de aceitação
- AC1: contratos distinguem página, vazio e cada categoria de erro.
- AC2: cliente preserva cursor e bruto, sem retry escondido.
- AC3: fixtures reproduzem páginas e falhas de maneira determinística.
- AC4: credenciais e corpo bruto não vazam em logs de erro.

## Testes necessários
AC1/AC2: HTTPX MockTransport para 200 válido/vazio, 400, 401, 403, 429, 5xx, timeout, JSON inválido e sort ausente em página paginada. AC3: repetição de fixtures e consulta por número. AC4: captura de logs.

### Revisão em 10/10/2026

- O índice TJGO armazena `dataAjuizamento` como `YYYYMMDDHHMMSS`. Um filtro de intervalo com datas ISO (`2026-05-01`) é aceito pelo endpoint, mas não casa nenhum documento; a mesma semana em formato compacto retornou 17.817 processos. O payload agora serializa os limites do intervalo semiaberto como `YYYYMMDD000000`.
- O sort padrão passou a `@timestamp` asc + `id.keyword` asc, o mesmo validado na paginação real (SPEC-003). O contrato aceita somente esses campos, exige `@timestamp` primeiro e rejeita cursor com quantidade de valores diferente do sort antes do HTTP. Snapshots persistidos com sort de um termo continuam retomáveis; a atualização por CNJ compara a consulta ignorando o sort legado. Como o sort integra o `operation_key`, jobs novos não coalescem com jobs ativos antigos equivalentes. A fixture demo emite um valor por termo do sort.
- O read timeout de 20 s foi insuficiente: respostas reais levaram de 8 a 32 s em 10/10/2026 e 57 s na amostra de 09/10/2026. O padrão passou a 60 s configurável. Heartbeats usam sessão própria e o limite real permanece abaixo do lease, evitando que uma chamada bloqueada ultrapasse a posse do job.

## Erros e edge cases
Total com relation=gte é limite inferior, não denominador exato. Resposta sem movimentos preserva ausência, distinta de lista vazia. Consulta inválida falha antes de HTTP. Retorno de outro tribunal não será aceito silenciosamente na normalização.

## Fora do escopo
Retries, tabela de jobs, persistência, prova de compatibilidade TJGO e deduplicação de movimentos.

## Evidência e conclusão
Concluída em 08/10/2026. Critérios locais atendidos; sem probe ou coleta real nesta entrega.

- **AC1 — PASSOU:** página/vazio e categorias VALIDATION, AUTHENTICATION, AUTHORIZATION, RATE_LIMIT, NETWORK, SOURCE_UNAVAILABLE e CONTRACT são tipadas; status HTTP e `Retry-After` são preservados quando presentes.
- **AC2 — PASSOU:** MockTransport confirmou endpoint e corpo TJGO, envio do cursor recebido, bruto/metadados e uma chamada por tentativa. Envelope inválido gera CONTRACT; payload individual inválido permanece bruto para normalização/quarentena posterior.
- **AC3 — PASSOU:** fixtures cobrem páginas, cursores opacos, repetição, vazio explícito, mutação entre execuções, cursores inválidos/repetidos e erros injetáveis.
- **AC4 — PASSOU:** captura de logs e mensagens verificou ausência de chave, Authorization e corpo remoto; nenhuma resposta sensível foi usada em fixtures.

Validação executada em container Python 3.14.8 com PostgreSQL 18.6 isolado:

```text
docker compose --project-name agrojud-test --env-file .env.test -f compose.test.yaml --profile test run --rm --build tests sh -lc 'uv run --no-sync ruff check src tests && uv run --no-sync ruff format --check src tests && uv run --no-sync mypy src && uv run --no-sync pytest'
Ruff: All checks passed; 28 files already formatted.
mypy: Success: no issues found in 20 source files.
pytest: 57 passed.
```

Validações complementares: `docker compose config --quiet` passou para demo, real (`.env.real.example`) e teste; `docker compose --project-name agrojud-demo --env-file .env.demo -f compose.yaml build api worker` construiu a imagem de produção; import dos adaptadores dentro da imagem de produção passou.

Não houve migration nem mudança de schema. HTTP foi exclusivamente simulado; nenhuma chamada ao CNJ foi feita. A correspondência observada em fixtures não valida shape, IDs, filtros, ordenação composta nem paginação reais. Esses aceites permanecem na SPEC-003; DONE habilita desenvolvimento local e não ativa coleta real.

