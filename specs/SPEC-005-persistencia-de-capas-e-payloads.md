# SPEC-005 — Persistência de capas e payloads

Status: DONE

Milestone/Spike: M2

## Dependências

[SPEC-002](SPEC-002-contratos-e-adaptadores-de-fonte.md)

Referências: [Plano](../IMPLEMENTATION_PLAN.md) e [PRD](../PRD.md). Executar somente esta unidade; não implementar suas sucessoras.

## Objetivo
Persistir uma página de capas e payloads com rastreabilidade e efeitos idempotentes.

## Contexto
M2 ocorre antes dos jobs. A identificação de coleta precisa existir sem depender da fila futura.

## Escopo
Migrations, modelos e repositórios de processo, representação, versão, coleta e observação; serviço de ingestão sem HTTP.

## Requisitos técnicos e contratos
- UUIDs locais; processo tem numero_cnj textual de 20 dígitos único. Não reconstruir número a partir de identificador composto.
- Representação única por fonte + tribunal + identificador de origem. Se a mesma chave passar a apontar para CNJ diferente, rejeitar com erro de integridade; não reassociar silenciosamente.
- Versão: representação, hash do JSON canônico, bruto JSONB, versão do normalizador, datas da fonte e primeira observação; unique(representação, hash).
- Canonicalização bruta: SHA-256 de JSON UTF-8 com chaves ordenadas e sem espaços insignificantes, preservando tipos, null e ordem dos arrays. Reordenação pode criar outra versão bruta; identidade de movimentos é responsabilidade separada de SPEC-004/006.
- Coleta: UUID, modo demo/real, critérios resolvidos JSONB, hash dos critérios, data de criação e coleta anterior opcional. Não possui estados de fila.
- Observação: coleta, chave da página, ordinal do hit, versão recebida e horário. Unique(coleta, chave_página, ordinal); replay com conteúdo diferente na mesma posição é conflito, não sobrescrita.
- Resultado por coleta/representação distinto das observações: permite explicar inclusão sem inflar quantidade de processos.
- Serviço recebe sessão/transação externa e não faz commit. Retorna contadores novos/atualizados/inalterados e identificação das versões.
- Campos estruturados: tribunal, classe, grau, órgão, ajuizamento e assuntos. Assuntos em relação própria para filtros, sem tornar o JSONB a única interface de consulta.
- Preservar datas originais e ambiguidade; valores parseáveis com fuso em timestamptz. Ausência não vira zero.
- Representação aponta à última versão observada localmente; expor separadamente eventual regressão da data informada pela fonte.
- Índices para CNJ, origem, assuntos, classe e órgão. Testes e banco operacional não compartilham schema.
- Validação real de identidade permanece condicionada à SPEC-003; fixtures possuem identidade explícita.

## Comportamento esperado
Uma nova coleta pode observar uma versão já existente sem copiá-la. Repetir a mesma página na mesma coleta não duplica observações ou resultados.

## Decisões importantes
Não criar job fictício para M2. A SPEC-007 adicionará vínculo entre job e coleta existente. Erro de hit nesta unidade é retornado ao chamador; armazenamento de quarentena pertence à SPEC-006.

## Implementação entregue
- A migration `20261009_0002` cria processos, representações, versões imutáveis, coletas, observações, resultados por representação e assuntos estruturados. Constraints garantem CNJ textual de 20 dígitos, identidade de origem, vínculo entre representação e versão mais recente, posição de hit, hash e associações.
- `agrojud.db.models` declara o schema; `agrojud.db.repositories` implementa criação concorrente idempotente e detecção de colisão; `agrojud.services.ingestion` ingere uma `SourcePage` sem HTTP e sem commit. A página usa savepoint dentro da transação aberta pelo chamador.
- `source` e `tribunal` mais `source_id` identificam uma representação; o processo é localizado somente pelo `numeroProcesso` validado, sem decompor IDs compostos. Uma identidade existente ligada a outro CNJ gera `SourceIdentityConflict`.
- O hash da capa e dos critérios usa `canonical_json` da SPEC-004. O payload bruto do `_source` fica em JSONB; array reordenado gera outra versão. Hash igual com JSON incompatível gera `PayloadHashCollisionError`.
- As posições são ordinais começando em 1. Replay exato preserva IDs, horário e classificação; conteúdo, identidade ou quantidade de hits diferente na mesma chave de página é conflito. Contadores representam representações distintas na página, usando o resultado persistido da primeira posição.
- `collection_results` mantém uma associação por coleta/representação, enquanto cada posição permanece em `collection_observations`. A versão observada mais recentemente aponta a projeção pesquisável de classe, grau, órgão, ajuizamento e relação própria de assuntos.
- Datas originais ficam no payload e em campos textuais. Instantes com fuso são gravados em UTC/timestamptz; horários parseáveis sem fuso são explicitamente marcados como ambíguos e não recebem fuso presumido. Regressão de ajuizamento fica sinalizada na observação com a versão anterior e também é retornada pelo serviço.
- O serviço não faz chamada externa, não cria job e não grava quarentena. Fixtures de teste declaram identidade explicitamente; nenhuma chamada real ao DataJud foi realizada.

## Critérios de aceitação
- **AC1 — PASSOU:** replay preservou as contagens de processo/representação/versão/observação/resultado/assunto em `1/1/1/1/1/1`, as mesmas associações e o contador `new=1`; não alterou horário observado.
- **AC2 — PASSOU:** segunda coleta criou a segunda observação e resultado, reutilizando o mesmo ID de versão; a contagem de versões permaneceu 1 e o contador foi `unchanged=1`.
- **AC3 — PASSOU:** duas identidades de origem do mesmo CNJ mantiveram um processo e duas representações distintas.
- **AC4 — PASSOU:** rollback externo removeu coleta e todos os efeitos da página. Um conflito após o primeiro hit também reverteu os efeitos parciais do savepoint, preservando somente os dados previamente confirmados.
- **AC5 — PASSOU:** migration `20261009_0002` aplicou em banco PostgreSQL vazio e sobre `20261008_0001` (SPEC-001).

## Testes necessários
PostgreSQL real para AC1–AC5; casos de conflito de origem/CNJ, hash igual com conteúdo incompatível e regressão temporal. Usar fixtures, sem HTTP remoto.

## Erros e edge cases
Hit sem identidade não cria processo parcial sem rastreabilidade. Erro de banco não é classificado como registro inválido. Falha depois de inserir processo deve fazer rollback da página inteira.

## Fora do escopo
Movimentos, quarentena, jobs, checkpoint, triagem e API de produto.

## Evidência e conclusão
Registrar migrations aplicadas, contagens antes/depois de replay e teste de rollback. DONE comprova somente a persistência local de capas.

### Validação executada em 09/10/2026

Execução em PostgreSQL 18.6 isolado no projeto Compose `agrojud-spec005`; cada teste criou banco descartável com sufixo `_test`. Os bancos de teste e operacional não compartilham schema ou volume.

```text
TEST_DB_HOST_PORT=55491 docker compose --project-name agrojud-spec005 --env-file .env.test -f compose.test.yaml --profile test run --rm --build tests sh -lc 'uv run --no-sync ruff check src tests && uv run --no-sync ruff format --check src tests && uv run --no-sync mypy src && uv run --no-sync pytest'
Ruff: All checks passed.
Ruff format: 41 files already formatted.
mypy: Success: no issues found in 29 source files.
pytest: 104 passed.
```

Os 12 testes de `test_persistence.py` passaram isoladamente. A suíte aplicou migrations em bancos vazios e a partir da revisão SPEC-001; conferiu replay e associações; verificou conflitos de origem/CNJ, posição e hash; e provou rollback externo e rollback do savepoint.

As configurações Compose demo, real (usando `.env.real.example`) e test passaram com `docker compose config --quiet`. `docker compose --project-name agrojud-spec005-prod --env-file .env.demo -f compose.yaml build api worker` construiu a imagem de produção.

Não houve acesso ao banco operacional ou ao DataJud. A evidência valida persistência local com fixtures sintéticas e não libera capacidades externas pendentes em SPEC-003. Movimentos e quarentena continuam sob SPEC-006; jobs, checkpoint, triagem e API permanecem fora do escopo.

