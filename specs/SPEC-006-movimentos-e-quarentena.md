# SPEC-006 — Movimentos e quarentena

Status: DONE

Milestone/Spike: M2

## Dependências

[SPEC-004](SPEC-004-identidade-e-reconciliacao.md), [SPEC-005](SPEC-005-persistencia-de-capas-e-payloads.md)

Referências: [Plano](../IMPLEMENTATION_PLAN.md) e [PRD](../PRD.md). Executar somente esta unidade; não implementar suas sucessoras.

## Objetivo
Completar ingestão local com movimentos e quarentena, preservando conteúdo inválido sem comprometer a atomicidade.

## Contexto
M2 usa a política aprovada pela SPEC-004 e o serviço transacional da SPEC-005.

## Escopo
Migrations e ingestão de ocorrências, associação às versões, diagnóstico de normalização e reprocessamento local de rejeições.

## Requisitos técnicos e contratos
- Ocorrência única por representação, versão do algoritmo, fingerprint e ordinal de multiplicidade; ligação versão_payload/ocorrência guarda presença naquele snapshot.
- Preservar first_observed_at, data do movimento, bruto do movimento e resultado de comparação. Não apagar ocorrências ausentes no snapshot seguinte.
- Normalização de uma representação é completa apenas se a lista de movimentos estiver presente, válida e todos os elementos exigidos forem processados. Lista vazia explícita é válida; ausente é incompleta.
- Quarentena: coleta, página, ordinal do hit, caminho do erro, bruto, código de validação, normalizador, horário e situação pending/resolved; unicidade da rejeição por localização e diagnóstico.
- Se capa é válida e movimento não, persistir capa/bruto e rejeição localizada, marcar normalização incompleta. Não declarar baseline completo.
- Se identidade da capa é inválida, preservar hit em quarentena sem criar representação presumida.
- Serviço processa registros válidos e rejeições na mesma transação externa. Falha em gravar quarentena aborta a página.
- Reprocessamento por comando local com IDs explícitos, normalizador versionado e transação por hit; reaproveitar identidades e versões. Resolução acrescenta histórico, não apaga rejeição original.
- Reprocessamento não altera o resultado histórico da coleta e não avança checkpoint remoto. Contagens operacionais futuras podem informar rejeições resolvidas separadamente.

## Comportamento esperado
Rejeição de um movimento fica visível sem perda do payload. Replay não repete quarentena. Correção do normalizador permite resolver itens sem consultar o DataJud.

## Decisões importantes
Quarentena é falha de dados/contrato identificável; erros SQL e bugs inesperados abortam, não são mascarados como rejeição de registro.

## Critérios de aceitação
- AC1: política de multiplicidade/reordenação da SPEC-004 preservada no banco.
- AC2: rejeições e dados válidos confirmam ou revertem juntos.
- AC3: ausência de movimentos não equivale a captura histórica completa.
- AC4: replay e reprocessamento não duplicam ocorrências/rejeições.
- AC5: resolução mantém auditoria e resultado original da coleta.

## Testes necessários
AC1: duas capas, ocorrências iguais e arrays permutados. AC2: falha injetada na gravação de quarentena. AC3: ausente/null/vazio/inválido. AC4/AC5: repetir reprocessamento com normalizador corrigido.

## Erros e edge cases
Data ambígua preservada sem ordenação cronológica inventada. Campo extra permanece no bruto. Remoção no snapshot não exclui histórico. Reprocessar ID inexistente gera erro claro e não varre toda a quarentena.

## Fora do escopo
Fila de reprocessamento, sinais jurídicos, baseline, API de quarentena e limpeza automática.

## Implementação entregue

- A migration `20261009_0003` cria identidades de ocorrência por representação, versão do normalizador, fingerprint e ordinal; snapshots por versão de payload; associações com presença e comparação; rejeições localizadas; e histórico de resolução. Constraints protegem multiplicidade, representação, versão e estado de resolução. Os índices atendem histórico por data e consulta operacional da quarentena.
- `ingest_page` normaliza os movimentos dentro do savepoint já aberto pela página. Capa válida com movimento rejeitado continua persistida, recebe snapshot incompleto e diagnóstico bruto localizado. Capa inválida gera quarentena sem criar processo ou representação presumida. Falhas de banco propagam e revertem os efeitos da página.
- Ausência, `null`, tipo não lista e elemento inválido deixam o snapshot incompleto; lista vazia explícita é completa. Snapshots incompletos não registram ausência dos movimentos históricos, e snapshots completos registram a comparação sem excluir ocorrências antigas.
- O comando `uv run --no-sync agrojud-quarantine-reprocess --id <UUID> [--id <UUID> ...] [--normalizer-version <versão>]` recebe somente IDs explícitos e executa uma transação por hit. Não faz HTTP e não altera observações ou resultados históricos da coleta. Ocorrências criadas ao reprocessar uma versão corrigida preservam o horário original de primeira observação; o snapshot registra separadamente o horário do processamento local. A resolução mantém a rejeição original e acrescenta evento de auditoria.
- Normalizações são registradas por versão, inclusive snapshots vazios. O comando aceita somente versões registradas no processo; uma correção deve ser implantada como nova versão antes de reprocessar.

## Critérios de aceitação

- **AC1 — PASSOU:** PostgreSQL mantém ordinais de multiplicidade por representação e normalizador. Arrays permutados reutilizam ocorrências; duas representações do mesmo CNJ mantêm conjuntos próprios; alteração descritiva é armazenada como `ALTERATION_OBSERVED` com referência de comparação. Data original, data UTC e ambiguidade de fuso foram verificadas.
- **AC2 — PASSOU:** falha injetada ao gravar quarentena reverteu processo, representação, versão, observação, resultado, snapshot e ocorrência da página.
- **AC3 — PASSOU:** testes cobriram campo ausente, `null`, tipo não lista, item inválido e lista vazia. Capa válida permaneceu observada quando seus movimentos eram incompletos; não houve associação de ausência em snapshot incompleto. Capa de identidade inválida permaneceu no bruto da quarentena sem representação.
- **AC4 — PASSOU:** replay de página válida com rejeição de movimento e replay de capa inválida mantiveram IDs e contagens. Reprocessamento com versão corrigida persistiu snapshots/identidades dessa versão e a repetição não criou efeitos adicionais.
- **AC5 — PASSOU:** a resolução acrescentou evento de auditoria e mudou a situação da rejeição preservando o bruto original e os resultados/observações da coleta. Reprocessamento com uma nova versão preservou a primeira observação e registrou o processamento local no snapshot. ID inexistente produziu erro claro sem varredura ampla.

## Evidência e conclusão
Validação executada em 09/10/2026 em PostgreSQL 18.6 isolado, Python 3.14.8 e fixtures sintéticas:

```text
TEST_DB_HOST_PORT=55496 docker compose --project-name agrojud-spec006 --env-file .env.test -f compose.test.yaml --profile test run --rm --build tests sh -lc 'uv run --no-sync ruff check src tests && uv run --no-sync ruff format --check src tests && uv run --no-sync mypy src && uv run --no-sync pytest'
Ruff: passou.
Ruff format: 48 arquivos já formatados.
mypy: nenhum problema em 34 arquivos fonte.
pytest: 126 passaram.
```

A suíte aplicou migrations em banco vazio e a partir de `20261009_0002`; os testes anteriores de persistência também aplicaram a partir de `20261008_0001`. Configurações Compose demo/real/test passaram; a imagem de produção API/worker foi construída. `uv run --no-sync agrojud-quarantine-reprocess --help` confirmou a interface local do comando.

Não houve acesso ao banco operacional ou ao DataJud. Os testes demonstram somente persistência e reprocessamento locais com dados sintéticos; não aprovam identidade ou estabilidade de movimentos reais. No fechamento da SPEC-006, a SPEC-007 aguardava esta dependência; a unidade foi concluída depois conforme [sua evidência](SPEC-007-jobs-leases-e-posse.md).

