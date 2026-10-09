# SPECs — AgroJud Radar

Este índice organiza as unidades executáveis do [IMPLEMENTATION_PLAN](../IMPLEMENTATION_PLAN.md), respeitando o [PRD](../PRD.md). Data da divisão: 08/10/2026. SPEC-001, SPEC-002 e a unidade de ferramenta/testes/relatório da SPEC-003 foram concluídas em 08/10/2026. SPEC-004 a SPEC-008 foram concluídas localmente em 09/10/2026, incluindo reconciliação, persistência, fila/leases e paginação/checkpoints transacionais. As evidências usam fixtures/HTTP sintéticos; uma amostra manual confirmou envelope e campos de um hit, enquanto filtro, sort e paginação reais seguem sem validação externa.

## Como executar uma unidade

1. Ler a SPEC selecionada e os contratos específicos de suas dependências citadas.
2. Confirmar no código e nas evidências que dependências estão concluídas; a existência de um documento não satisfaz a dependência.
3. Reavaliar o status da unidade. Não iniciar comportamento real sem suas capacidades aprovadas.
4. Implementar apenas o escopo da unidade, com migrations, testes e documentação que ela exige.
5. Registrar critérios satisfeitos, comandos e resultados. Atualizar o status no documento e neste índice sem marcar sucessoras automaticamente.

Uma SPEC contém objetivo, contexto, dependências, escopo, requisitos/contratos, comportamento, decisões, critérios numerados, testes, erros/edge cases, exclusões e evidências. Contratos do produto prevalecem sobre conveniência de implementação; inconsistência descoberta deve ser registrada, não contornada silenciosamente.

## Status e validações

- **READY:** informações e dependências necessárias disponíveis; autorização de implementação não significa conclusão.
- **BLOCKED_DEPENDENCY:** depende de implementação predecessora ainda não entregue.
- **BLOCKED_VALIDATION:** implementação necessária existe, mas falta evidência técnica externa ou decisão explicitamente exigida.
- **IN_PROGRESS:** execução iniciada.
- **DONE:** todos os critérios desta unidade foram atendidos e registrados.

SPEC-001 a SPEC-008 estão DONE localmente. SPEC-005/006 comprovam efeitos locais de capas, movimentos e quarentena; SPEC-007/008 comprovam fila, posse e paginação/checkpoints em PostgreSQL isolado com HTTP simulado. A fonte DataJud real permanece bloqueada porque S2 segue INCONCLUSIVE. SPEC-009 está READY após a conclusão da dependência direta; somente seu status foi reavaliado, seu escopo ainda não foi iniciado. SPEC-010 está READY porque sua dependência direta está entregue; READY autoriza iniciar a unidade, sem validar capacidades externas. SPEC-003 entregou probe/testes/relatório. Uma amostra manual confirmou envelope, campos essenciais e igualdade de `_id`/`_source.id` para um hit; filtros, busca exata, sort e paginação permanecem INCONCLUSIVE. SPEC-004/006 fixam a política técnica local com fixtures sintéticas; não validam movimentos reais nem liberam a fonte DataJud. SPEC-010 continua responsável pelo catálogo versionado.

### Dois controles distintos

Status da SPEC mede a entrega da unidade. Capacidades externas têm estado VALIDATED, INCOMPATIBLE ou INCONCLUSIVE, com evidência e data.

SPEC-003 está DONE como unidade de implementação. A probe limitada expirou; uma amostra manual posterior confirmou apenas envelope, campos essenciais e identidade para um hit. Filtro, busca exata, sort e paginação continuam pendentes, portanto S1/S2 não liberam a fonte real. SPEC-010 pode entregar catálogo/investigação com itens inconclusivos sem aprovar S5 por completo. DONE nesses documentos não libera capacidades reais pendentes.

SPEC-005 a SPEC-019 podem comprovar seus efeitos locais em demo, conforme seus critérios; isso não encerra os aceites reais. SPEC-020 exige os aceites reais pertinentes para DONE integral e fica BLOCKED_VALIDATION se faltar essa evidência. Nunca substituir erro externo por fonte sintética dentro de uma execução real.

## Índice e dependências de implementação

| SPEC | Milestone/Spike | Dependências diretas | Status atual |
| --- | --- | --- | --- |
| [SPEC-001 — Fundação local](SPEC-001-fundacao-local.md) | M0 | Nenhuma | DONE |
| [SPEC-002 — Contratos e adaptadores de fonte](SPEC-002-contratos-e-adaptadores-de-fonte.md) | M1 | [SPEC-001](SPEC-001-fundacao-local.md) | DONE |
| [SPEC-003 — Validação DataJud/TJGO](SPEC-003-validacao-datajud-tjgo.md) | S1/S2 | [SPEC-002](SPEC-002-contratos-e-adaptadores-de-fonte.md) | DONE |
| [SPEC-004 — Identidade e reconciliação](SPEC-004-identidade-e-reconciliacao.md) | S3 | [SPEC-002](SPEC-002-contratos-e-adaptadores-de-fonte.md) | DONE |
| [SPEC-005 — Persistência de capas e payloads](SPEC-005-persistencia-de-capas-e-payloads.md) | M2 | [SPEC-002](SPEC-002-contratos-e-adaptadores-de-fonte.md) | DONE |
| [SPEC-006 — Movimentos e quarentena](SPEC-006-movimentos-e-quarentena.md) | M2 | [SPEC-004](SPEC-004-identidade-e-reconciliacao.md), [SPEC-005](SPEC-005-persistencia-de-capas-e-payloads.md) | DONE |
| [SPEC-007 — Jobs, leases e posse](SPEC-007-jobs-leases-e-posse.md) | M3 | [SPEC-006](SPEC-006-movimentos-e-quarentena.md) | DONE |
| [SPEC-008 — Paginação e checkpoints](SPEC-008-paginacao-e-checkpoints.md) | M4 | [SPEC-007](SPEC-007-jobs-leases-e-posse.md) | DONE |
| [SPEC-009 — Retries e recuperação](SPEC-009-retries-e-recuperacao.md) | M4/S4 | [SPEC-008](SPEC-008-paginacao-e-checkpoints.md) | READY |
| [SPEC-010 — Catálogo temático versionado](SPEC-010-catalogo-tematico-versionado.md) | S5/M1 | [SPEC-002](SPEC-002-contratos-e-adaptadores-de-fonte.md) | READY |
| [SPEC-011 — API operacional e OpenAPI](SPEC-011-api-operacional-e-openapi.md) | M5 | [SPEC-009](SPEC-009-retries-e-recuperacao.md), [SPEC-010](SPEC-010-catalogo-tematico-versionado.md) | BLOCKED_DEPENDENCY |
| [SPEC-012 — Frontend de coleta e consulta](SPEC-012-frontend-de-coleta-e-consulta.md) | M6 | [SPEC-011](SPEC-011-api-operacional-e-openapi.md) | BLOCKED_DEPENDENCY |
| [SPEC-013 — Triagem e histórico humano](SPEC-013-triagem-e-historico-humano.md) | M7 | [SPEC-012](SPEC-012-frontend-de-coleta-e-consulta.md) | BLOCKED_DEPENDENCY |
| [SPEC-014 — Sinais e reprocessamento local](SPEC-014-sinais-e-reprocessamento-local.md) | M7 | [SPEC-013](SPEC-013-triagem-e-historico-humano.md) | BLOCKED_DEPENDENCY |
| [SPEC-015 — Acompanhamento manual](SPEC-015-acompanhamento-manual.md) | M8 | [SPEC-014](SPEC-014-sinais-e-reprocessamento-local.md) | BLOCKED_DEPENDENCY |
| [SPEC-016 — Referência histórica e novidades](SPEC-016-referencia-historica-e-novidades.md) | M8 | [SPEC-015](SPEC-015-acompanhamento-manual.md) | BLOCKED_DEPENDENCY |
| [SPEC-017 — Buscas salvas e agendamento](SPEC-017-buscas-salvas-e-agendamento.md) | M8 | [SPEC-016](SPEC-016-referencia-historica-e-novidades.md) | BLOCKED_DEPENDENCY |
| [SPEC-018 — Indicadores da base local](SPEC-018-indicadores-da-base-local.md) | M9 | [SPEC-017](SPEC-017-buscas-salvas-e-agendamento.md) | BLOCKED_DEPENDENCY |
| [SPEC-019 — Exportação CSV](SPEC-019-exportacao-csv.md) | M9 | [SPEC-017](SPEC-017-buscas-salvas-e-agendamento.md) | BLOCKED_DEPENDENCY |
| [SPEC-020 — Operação e aceite integrado](SPEC-020-operacao-e-aceite-integrado.md) | M9 | [SPEC-018](SPEC-018-indicadores-da-base-local.md), [SPEC-019](SPEC-019-exportacao-csv.md) | BLOCKED_DEPENDENCY |

As dependências transitivas estão implícitas no grafo acima. Dependências de capacidades reais são adicionais: identidade/consultas/sort por SPEC-003 e presets/regras por SPEC-010. Não há dependência circular entre transporte, catálogo e persistência.

## Ordem e Critical Path

Ordem recomendada: **001 → 002 → 003 → 004 → 005 → 006 → 007 → 008 → 009 → 010 → 011 → 012 → 013 → 014 → 015 → 016 → 017 → 018 → 019 → 020**.

SPEC-010 pode ser antecipada após 002; SPEC-018 e SPEC-019 são independentes após 017. Esta independência não exige execução por agentes paralelos.

Caminho do núcleo local: **001 → 002 → (004 + 005) → 006 → 007 → 008 → 009 → 011 → 012**, com catálogo 010 necessário para 011.

Caminho jurídico: **012 → 013 → 014 → 015 → 016 → 017**.

Aceite: **017 → (018 + 019) → 020**, mais validações reais de 003/010.

A indisponibilidade do TJGO permite desenvolver o núcleo local com fixtures, mas não alterar o status das capacidades reais. Reexecutar probes somente de forma limitada e quando houver motivo para nova verificação.

## Cobertura do plano

| Parte do plano | SPECs responsáveis |
| --- | --- |
| M0 | 001 |
| M1 | 002 e 010; validação externa em 003 |
| M2 | 005 e 006; política de identidade em 004 |
| M3 | 007 |
| M4 | 008 e 009 |
| M5 | 011 |
| M6 | 012 |
| M7 | 013 e 014 |
| M8 | 015, 016 e 017 |
| M9 | 018, 019 e 020 |
| S1 e S2 | 003 |
| S3 | 004 |
| S4 | 009, apoiada pelos testes de 007/008 |
| S5 | 010 |

## Decisões registradas nesta divisão

- **Coleta antes de job:** SPEC-005 cria identificação independente da coleta; SPEC-007 a vincula ao job. Evita depender de uma fila inexistente em M2.
- **Baseline por representação válida:** SPEC-016 só usa snapshots com lista de movimentos normalizada integralmente; rejeição mantém referência pendente. Completude técnica não garante completude jurídica.
- **Correção não é novo ato:** SPEC-004 define correspondência exata e alteração observada, sem inferência jurídica e sem fuzzy merge.
- **Habilitação por capacidade:** transporte/paginação (003) e taxonomia/evidência temática (010) têm resultados independentes.
- **Rejeições:** SPEC-006 resolve quarentena localmente com auditoria; continuar páginas não apaga rejeições ou reescreve resultado histórico.
- **Temporização:** SPEC-002 estabelece timeouts; SPEC-007 lease/heartbeat e limites de transação; SPEC-009 backoff, cooldown e recuperação limitada.
- **Exportação limitada:** SPEC-019 fixa orçamento síncrono de 50.000 processos, configurável e sem truncamento silencioso. É limite operacional separado do orçamento de coleta.
- **Schema e interfaces incrementais:** APIs jurídicas e migrations correspondentes são introduzidas em suas SPECs, não antecipadas na fundação.

## Contratos transversais mínimos

- API e worker síncronos, mesmo pacote/imagem, processos separados; PostgreSQL como fila.
- Banco real de teste isolado; testes do contrato HTTP usam fonte simulada. CI comum não depende do CNJ.
- HTTP fora de transação e efeitos de página/checkpoint atômicos sob posse válida.
- Identidade do processo não substitui identidade de representação ou movimento.
- Datas do evento, fonte e observação local têm significados separados.
- Sem autenticação somente em localhost, um operador. Segredos não entram em browser, Git ou logs.
- Getters não persistem projeções/defaults. Ingestão não modifica decisões humanas.
- Uma capacidade habilitada requer evidência compatível com sua consulta/versão; número de testes verdes não substitui a evidência remota.
- Nenhuma feature do Deferred Work entra por conveniência técnica.

## Checklist documental

- [x] 20 unidades com escopo, dependências e status inicial.
- [x] M0–M9 e S1–S5 mapeados.
- [x] Esclarecimentos registrados sem alterar o PRD.
- [ ] Implementação das unidades — acompanhar status individual, não marcar por geração de documentos.

