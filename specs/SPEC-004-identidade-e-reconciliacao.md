# SPEC-004 — Identidade e reconciliação

Status: DONE

Milestone/Spike: S3

## Dependências

[SPEC-002](SPEC-002-contratos-e-adaptadores-de-fonte.md)

Referências: [Plano](../IMPLEMENTATION_PLAN.md) e [PRD](../PRD.md). Executar somente esta unidade; não implementar suas sucessoras.

## Objetivo
Fixar e testar a identidade técnica de ocorrências, sem inventar identidade jurídica.

## Contexto
S3 resolve um risco anterior à persistência de movimentos. Não existe identificador global comprovado de movimentação no projeto.

## Escopo
Funções puras de canonicalização/comparação, fixtures sintéticas e documento de decisão. Sem banco e sem fonte remota.

## Requisitos técnicos e contratos
- Hash SHA-256 de JSON canônico com chaves ordenadas, codificação UTF-8 e sem dependência de espaços. Preservar tipos, ausente versus null e valores originais no bruto.
- Hash bruto de payload preserva ordem dos arrays: reordenação pode gerar versão de bruto distinta, mas nunca novas ocorrências por si só.
- Normalização de movimento: código, data/hora original e normalizada quando interpretável, nome, órgão, complementos e campos extras. Ordenar complementos para comparação, preservando sua multiplicidade.
- Identidade exata: representação + hash do movimento normalizado + ordinal 1..N entre conteúdos idênticos. Não usar posição no array original.
- Comparação usa multiconjuntos. Manter histórico de ocorrências já observadas; sumiço não exclui ocorrência e reaparecimento exato não é novo.
- Chave auxiliar de comparação: código + data/hora + órgão + complementos estruturados, sem nome descritivo. Serve para identificar alteração potencial, nunca para mesclar registros.
- Se o conteúdo muda mantendo chave auxiliar, emitir ALTERATION_OBSERVED com referência aos conteúdos envolvidos. Múltiplas correspondências permanecem ambíguas.
- Conteúdo sem correspondência prévia é FIRST_OBSERVED; conteúdo exato anterior é KNOWN; ausência atual é NOT_PRESENT_IN_SNAPSHOT. Nenhum rótulo significa novo ato jurídico.
- Data sem fuso não ganha fuso presumido; armazenar ambiguidade e usar valor original na comparação.

## Comportamento esperado
Reordenar movimentos/complementos não gera novidades. Duplicatas legítimas preservam contagem. Alteração de nome não apaga versão anterior nem produz conclusão de novo ato.

## Decisões importantes
Versão do algoritmo faz parte dos metadados da normalização. Mudança futura exige reprocessamento explícito; nunca recalcular identidades silenciosamente.

## Critérios de aceitação
- AC1: canonicalização é determinística e diferencia null, ausente, número e texto.
- AC2: multiplicidade é preservada sem depender da ordem.
- AC3: reaparecimento exato é KNOWN; alteração textual é alteração observada.
- AC4: representações diferentes nunca têm suas ocorrências fundidas.

## Testes necessários
AC1: tabela de casos e estabilidade entre execuções. AC2: permutações e duplicatas 1→2→1→2. AC3: nome alterado, campo extra, complemento alterado e pares ambíguos. AC4: dois graus e dois órgãos do mesmo CNJ.

## Implementação entregue
- `agrojud.domain.canonical_json` implementa JSON UTF-8 canônico, chaves ordenadas, sem espaços estruturais e com preservação de tipos, `null`, campos ausentes e ordem dos arrays; rejeita valores não JSON e números não finitos.
- `agrojud.domain.occurrence_identity` implementa normalização versionada, ordinal determinístico por multiconjunto, histórico cumulativo por representação, estados técnicos e grupos de alteração com ambiguidade explícita.
- Datas com fuso são normalizadas para UTC; datas sem fuso mantêm o valor original como chave de comparação e recebem metadado de ambiguidade. Campos extras e valores originais permanecem no conteúdo normalizado.
- Fixtures exclusivamente sintéticas estão em [`occurrence_reconciliation.json`](../backend/tests/fixtures/occurrence_reconciliation.json). A decisão detalhada está em [`S3-identidade-e-reconciliacao.md`](../docs/decisions/S3-identidade-e-reconciliacao.md).
- Não foram criados schema, migration, persistência, chamadas HTTP ou adaptação da fonte.

## Critérios de aceitação e evidência
- **AC1 — PASSOU:** canonicalização é repetível; casos cobrem chaves ausentes versus `null`, inteiro versus texto, inteiro versus decimal, booleano versus número, Unicode e arrays ordenados.
- **AC2 — PASSOU:** reordenação de movimentos/complementos mantém identidades, o hash bruto continua sensível à ordem de arrays e duplicatas preservam ordinais em `1→2→1→2`.
- **AC3 — PASSOU:** mudança de nome e de campo extra gera `ALTERATION_OBSERVED`; complemento alterado não é associado pela chave auxiliar; retorno exato é `KNOWN`; múltiplas versões candidatas ficam ambíguas; ausências permanecem históricas.
- **AC4 — PASSOU:** o mesmo caso sintético em dois graus e dois órgãos mantém quatro referências e identidades distintas.
- Datas sem fuso são comparadas pelo original; datas com fuso mantêm o original e também recebem normalização UTC.

Validação executada em container Python 3.14.8 com PostgreSQL de teste isolado:

```text
TEST_DB_HOST_PORT=55491 docker compose --project-name agrojud-spec004 --env-file .env.test -f compose.test.yaml --profile test run --rm --build tests sh -lc 'uv run --no-sync ruff check src tests && uv run --no-sync ruff format --check src tests && uv run --no-sync mypy src && uv run --no-sync pytest'
Ruff: All checks passed.
Ruff format: 34 files already formatted.
mypy: Success: no issues found in 23 source files.
pytest: 92 passed.
```

`docker compose --project-name agrojud-spec004-prod --env-file .env.demo -f compose.yaml build api worker` concluiu, e os módulos foram importados pela imagem de produção via `uv run --no-sync`.

O banco isolado ficou disponível e saudável durante a suíte; esta SPEC não fez alterações nele. Não houve acesso ao DataJud. Os critérios demonstram o algoritmo local e não validam a identidade ou estabilidade de movimentos reais.

## Erros e edge cases
Código/data ausentes impossibilitam classificação jurídica, mas bruto continua preservável. Colisão de hash com conteúdo divergente deve ser detectada na persistência e tratada como integridade, não deduplicação.

## Fora do escopo
Fuzzy matching, deduplicação entre graus, interpretação jurídica, sinais, baseline e banco.

## Evidência e conclusão
Entregar tabela entrada/resultado e testes da política. DONE fixa o contrato utilizado por SPEC-006 e SPEC-016; não depende de amostra real para comprovar as propriedades locais.

