# SPEC-026 — Coleta multitribunal de grande volume

Status: DONE — AC1–AC10 atendidos em 13/10/2026; benchmark local e probe limitada TJGO registrados, com escopo externo restrito ao endpoint, preset e janela observados.

Data: 13/10/2026

Milestone: Pós-M9 / eficiência operacional multitribunal

Dependências: [SPEC-007 — Jobs, leases e posse](SPEC-007-jobs-leases-e-posse.md), [SPEC-008 — Paginação e checkpoints](SPEC-008-paginacao-e-checkpoints.md), [SPEC-009 — Retries e recuperação](SPEC-009-retries-e-recuperacao.md), [SPEC-011 — API operacional e OpenAPI](SPEC-011-api-operacional-e-openapi.md), [SPEC-012 — Frontend de coleta e consulta](SPEC-012-frontend-de-coleta-e-consulta.md), [SPEC-017 — Buscas salvas e agendamento](SPEC-017-buscas-salvas-e-agendamento.md) e [SPEC-025 — Suporte multitribunal](SPEC-025-suporte-multitribunal.md).

## Objetivo

Reduzir o tempo e a quantidade de chamadas das coletas de grande volume, aproveitando a capacidade pública do DataJud de devolver até 10.000 registros por página e continuar a consulta com search_after.

Não impor teto global de registros nem duração por decisão histórica do AgroJud. Manter limite manual opcional, retomada, idempotência, conclusão parcial, isolamento por tribunal, fairness da fila e controle global de requests.

A capacidade da fonte não representa garantia de que qualquer consulta, tribunal ou preset retornará resultados ou estará disponível.

## Fontes e limites da origem

Pesquisa documental concluída em 13/10/2026. Limites oficiais e decisões operacionais do AgroJud são distintos:

| Tema | Limite ou orientação da fonte | Decisão do AgroJud |
| --- | --- | --- |
| Registros por página | A Wiki oficial DataJud documenta size de 10 a 10.000 registros por página e recomenda search_after para percorrer grandes volumes. | Para novas coletas, aceitar 10–10.000. O benchmark selecionou 5.000 como padrão; 10.000 permanece máximo suportado e foi validado em probe limitada. Esse é um limite por request, não o total da coleta. |
| Total de resultados | Não foi encontrado teto total de registros na documentação oficial consultada. hits.total.value igual a 10.000 com relation gte indica contagem mínima, não o fim da paginação. | Sem teto automático de registros ou duração. hit_budget pode ser informado pelo operador; ausente ou nulo significa ilimitado. |
| Requests | O Termo de Uso atualmente ligado pela Wiki limita a 120 requests por minuto sem autorização escrita. O CNJ pode reduzir a cota por usuário/chave sem aviso e veda sobrecarga. | Limitador PostgreSQL compartilhado por todas as coletas DataJud que usam a chave configurada. Nunca exceder 120 requests em qualquer janela móvel de 60 segundos; retries também contam. |
| Paginação de grande volume | DataJud recomenda search_after. Elasticsearch exige a mesma consulta e ordenação entre páginas; from/size tem janela padrão de 10.000 e não é a estratégia de paginação profunda. | Continuar usando query e sort congelados, cursor sort completo e checkpoint. Só resposta válida com hits vazio confirma exaustão. |

### Fontes oficiais consultadas

- [DataJud Wiki — Exemplo 3: paginação com search_after](https://datajud-wiki.cnj.jus.br/api-publica/exemplos/exemplo3/) — tamanho de página de 10 a 10.000; continuação recomendada para grandes volumes.
- [DataJud Wiki — API Elastic](https://datajud-wiki.cnj.jus.br/para-tribunais/Datajud/Api-elastic/) — exemplo de hits.total com value 10.000 e relation gte, além de paginação por cursor.
- [Portal CNJ — API Pública DataJud](https://www.cnj.jus.br/sistemas/datajud/api-publica/) — remete à documentação da Wiki e ao Termo de Uso.
- [Termo de Uso da API Pública DataJud v1.2](https://formularios.cnj.jus.br/wp-content/uploads/2023/11/Termos-de-uso-api-publica-V1.2.pdf), atualmente ligado pela Wiki — cláusulas 3.13–3.15: máximo de 120 requisições por minuto sem autorização escrita; possibilidade de limitação inferior por usuário ou chave; vedação a sobrecarregar ou prejudicar a API.
- [Portaria CNJ nº 374/2026](https://atos.cnj.jus.br/atos/detalhar/6972), vigente na data da pesquisa — atualiza as condições gerais de disponibilização e consumo; não substitui a regra numérica do Termo de Uso atualmente vinculado.
- [Elasticsearch — Paginate search results](https://www.elastic.co/docs/reference/elasticsearch/rest-apis/paginate-search-results) — diferencia a janela de from/size de search_after, requer consulta e sort estáveis e recomenda PIT para consistência de snapshot. A documentação pública do DataJud consultada não estabelece suporte a PIT; esta SPEC não presume esse recurso.
- [Elasticsearch — Track total hits](https://www.elastic.co/guide/en/elasticsearch/reference/8.19/search-your-data.html) — o total exato é limitado a 10.000 por padrão, podendo a resposta informar uma contagem mínima. Esse comportamento não limita a sequência de páginas.
- Como exemplo não normativo de consumidor DataJud, o [pacote R datajud](https://github.com/rfdornelles/datajud) interrompe paginação diante de cursor repetido ou duplicação e permite retomada por cursor. Isso informa guardas de implementação, não define limite oficial.

## Contexto e diagnóstico

Uma seleção de quatro presets para os 33 tribunais allowlisted pode gerar até 132 jobs independentes. A execução atual usa página e orçamento inicial menores que a capacidade documentada; um teto de 2.000 hits pode encerrar cada job como parcial antes da exaustão da consulta.

Antes desta implementação, o runtime documentado nas SPECs anteriores incluía:

- Um job, collection, checkpoint, cobertura e resultado independentes por tribunal e preset.
- Página padrão e máxima de 100 hits e orçamento de 2.000 hits por execução, com continuação limitada a 2.000 hits adicionais.
- Espaçamento operacional de um request por segundo, além de cooldown persistido quando a fonte limita requests.
- Ingestão atômica de página e checkpoint sob posse válida; HTTP externo fora da transação.
- Query e sort congelados, cursor completo de search_after, retries limitados e preservação de progresso após falha.

A página 10.000 diminui round-trips, mas pode aumentar payload, memória e duração da persistência por página. A mudança de padrão depende de benchmark local e validação externa limitada. O teto de 120 requests/minuto é agregado: não é multiplicado pelo número de tribunais, presets ou workers.

## Escopo

1. Elevar o contrato de página DataJud para 10–10.000 registros e selecionar o default novo com base no benchmark e na probe desta SPEC.
2. Remover 2.000 como teto automático absoluto. Tornar hit_budget opcional, ilimitado por padrão e cumulativo por collection/tribunal.
3. Continuar a consulta por search_after até exaustão confirmada, respeitando query, sort, checkpoint, retries e idempotência existentes.
4. Impor o limite DataJud de 120 requests/minuto com um limitador compartilhado e persistente entre todas as coletas que usam a chave.
5. Evitar leases enquanto o limitador ou cooldown estiverem ativos; manter a reserva atômica antes de cada request como proteção autoritativa.
6. Propagar os mesmos defaults e limites por coleta manual, refresh, grupo multitribunal, buscas salvas, agendamentos, API/OpenAPI e Radar.
7. Preservar a coleta em curso, snapshots antigos, progresso já confirmado e isolamento de falhas por tribunal.

Fora do escopo: aumentar concorrência remota, criar infraestrutura, consolidar tribunais em uma consulta, expandir allowlist ou afirmar disponibilidade nacional.

## Requisitos e contratos

### Página e interfaces de coleta

- O contrato DataJud de page_size aceita inteiros de 10 a 10.000. A validação aplica-se a endpoints de coleta, adaptadores, filtros de buscas salvas e parâmetros de execução; paginação local de listas do produto permanece independente.
- O default de novas execuções é 5.000 conforme a maior vazão média observada no benchmark local; a probe real confirma o contrato máximo de 10.000 e sua continuação. O tamanho permanece configurável dentro da faixa oficial.
- Um tamanho explícito menor continua válido dentro da faixa oficial; 100 e 500 não ficam como máximos permanentes por histórico.
- O tamanho é congelado por execução. Revisões antigas e jobs ativos mantêm seus snapshots; defaults novos não reescrevem parâmetros persistidos.
- A quantidade efetivamente ingerida por página é o mínimo entre page_size e o saldo de um limite manual cumulativo, quando informado. Se esse saldo for menor que 10, solicitar o mínimo DataJud de 10 e persistir apenas o saldo restante, avançando o checkpoint até o último hit efetivamente persistido.
- Exceder o tamanho solicitado, envelope inválido ou resposta incompatível com o contrato da fonte é erro explícito; nunca avança checkpoint.

### Paginação e encerramento

- Para cada request, manter a consulta e o sort congelados e enviar o cursor completo sort do último hit confirmado, conforme o contrato DataJud validado nas SPECs-003/008.
- Repetir a sequência até receber uma resposta válida cujo vetor hits.hits esteja vazio. Página menor que page_size, hits.total ou relation gte não prova exaustão.
- O campo hits.total é informativo. Não encerrar nem limitar a ingestão ao valor 10.000 quando relation for gte.
- Cada página confirmada grava ingestão, rejeições, contadores, evento e cursor/checkpoint atomicamente sob posse válida. A chamada HTTP ocorre fora da transação.
- Falha ao validar ou persistir uma página não avança o checkpoint. Replay do mesmo cursor não duplica capas, movimentos, rejeições ou contadores.
- Cursor ausente, inválido, repetido ou sem avanço é falha de contrato, não página vazia. Preservar a classificação parcial e a retomada/restart_scan da SPEC-008/009.
- A fonte DataJud é mutável e a API pública consultada não documenta PIT. Não prometer snapshot consistente: preservar a query/sort e idempotência, sinalizar as limitações de mutação já previstas e manter revarredura explícita conforme SPEC-008.

### Total por collection e duração

- Não existe teto AgroJud automático de hits nem duração total quando hit_budget está ausente/nulo.
- O limite manual, se informado, é cumulativo sobre hits de origem da collection daquele tribunal; hits rejeitados continuam contando, conforme SPEC-008. Não é um limite de grupo: cada membro continua isolado.
- Ao atingir limite manual, finalizar como partial com motivo limit e manter cursor/progresso. A continuação permite limpar o limite (nulo, sem teto) ou informar novo teto cumulativo maior que o total já confirmado; registrar a escolha no evento de continuação.
- A continuação não reinicia varredura nem zera contadores. Query, tribunal, preset, sort e último checkpoint confirmado são preservados.
- Não adicionar timeout global de horas nem máximo de páginas. A execução termina por página vazia confirmada, limite manual explícito, cancelamento, falha classificada ou controles de request/lease.
- Prevenção de loop infinito: não enviar request sem reserva de rate-limit; exigir cursor válido e avanço; falhar em cursor repetido; manter timeouts por request, retries limitados a cinco tentativas por página, lease/heartbeat, cancelamento e observabilidade de progresso. Se houver falha ou ausência de progresso, a collection fica parcial/retomável, nunca em sucesso vazio.
- O contrato de criação mantém hit_budget opcional e nulo como ilimitado. O comando de continuação permite limpar ou aumentar o teto cumulativo sem alterar o cursor; cliente antigo sem corpo continua a partir do checkpoint sob a política ilimitada.

### Frequência, limitador e fila

- A fonte documenta máximo de 120 requests/minuto. AgroJud aplica esse máximo globalmente por chave DataJud configurada, considerando tribunais, presets, grupos, jobs, workers e todas as tentativas HTTP, inclusive retries e respostas 429.
- Sem burst: exigir intervalo mínimo de 500 ms entre inícios de requests e também comprovar em teste que nenhuma janela móvel de 60 segundos supera 120 tentativas. A regra é por chave compartilhada, não por tribunal.
- A reserva existente em SourceRateLimit/record_http_attempt continua atômica e autoritativa, usando relógio do PostgreSQL. Nenhum request externo inicia antes da reserva confirmada.
- A elegibilidade pré-lease consulta next_request_at e cooldown_until. Se a janela ainda não permitir request, não conceder lease, criar tentativa, mudar progresso ou manter conexão/transação aberta para esperar.
- Jobs sintéticos não consomem a cota DataJud. O worker mantém o fluxo de espera e a fairness atual entre trabalho elegível.
- Respostas 429 e Retry-After atualizam o cooldown compartilhado e podem reduzir o ritmo abaixo do limite nominal. Não tentar contornar bloqueios por chave, tribunal, worker ou paralelismo.
- O intervalo histórico de um segundo era uma decisão operacional local, não o teto oficial. A implementação usa reserva compartilhada com intervalo mínimo de 500 ms e conserva retries, backoff, classificação de erros e cooldown da SPEC-009.

### Compatibilidade e execução ativa

- Não cancelar, reiniciar, clonar, reenfileirar ou editar a coleta operacional que motivou a SPEC. Não alterar checkpoints ou valores congelados de jobs existentes.
- Jobs ativos/antigos seguem com page_size e limite registrados em seus snapshots até chegarem a estado terminal. A semântica nova aplica-se a novas execuções e a uma continuação explicitamente solicitada após a atualização.
- Um grupo de quatro presets por todos os tribunais continua como jobs independentes por membro. Falha ou atraso de um tribunal não esconde o resultado dos outros.
- Nenhuma falha HTTP, 429, timeout, resposta malformada ou resultado parcial é convertida em lista vazia ou conclusão bem-sucedida.
- Não criar uma migration, fila, scheduler, worker paralelo ou novo serviço nesta etapa documental. A necessidade de schema só pode ser proposta se a implementação provar que o estado atual não consegue representar as invariantes.

## Benchmark e rollout

### Benchmark local obrigatório

- Executar em PostgreSQL isolado, sem dados operacionais, usando payloads sintéticos representativos e um conjunto fixo de pelo menos 25.001 hits. Comparar page_size 1.000, 5.000 e 10.000; esses perfis são pontos de medição, não limites permanentes.
- Para cada perfil e ao menos três execuções, registrar hits/minuto de ponta a ponta, requests, latência p50/p95, bytes de resposta simulados, tempo de parsing/validação/ingestão/checkpoint, RSS máximo, CPU, tamanho do banco e retries.
- Incluir páginas cheias, última página curta, página vazia, falha antes/depois do commit, replay, cancelamento, limite manual e retomada.
- Aprovar 10.000 como default somente se a execução completa passar sem OOM, timeout, erro de persistência ou violação de lease/posse e a vazão medida não for inferior ao perfil de 5.000. Registrar o limite de memória do ambiente medido; sem um envelope de memória declarado para o worker, o resultado de capacidade fica inconclusivo.
- Se 10.000 não atingir esse gate, selecionar entre os perfis íntegros o que apresentar maior vazão média dentro do envelope declarado, registrar o gargalo medido e manter 10.000 como opção explícita. Não restaurar silenciosamente 100, 500 ou 2.000 como limites permanentes sem evidência técnica.

### Probe DataJud limitada

- Executar somente depois de não haver coleta DataJud ativa, job pronto para claim nem dispatch agendado durante a janela da medição. Não concorrer com a execução operacional atual.
- Usar uma combinação allowlisted de um tribunal e um preset, com janela manual já permitida e consulta que possa retornar mais de 10.000 hits. Registrar tribunal, preset, critérios, data/hora e ambiente sem expor chave ou payload.
- Fazer no máximo 12 requests HTTP, sequenciais e somente leitura. Validar uma primeira página com 10.000 hits e ao menos uma continuação não vazia com search_after usando exatamente query e sort congelados. Não executar a probe nacional de quatro presets × todos os tribunais.
- Não fazer retries automáticos no benchmark. Parar na primeira resposta 429, Retry-After, timeout, erro de rede/5xx, autenticação recusada, cursor inválido ou sinal de degradação; classificar como inconclusivo/falha, nunca como zero resultados.
- Registrar request count, bytes, latência, hits, cursor e status. A probe valida somente o endpoint, consulta e momento observados; não certifica os 33 tribunais ou todos os presets.

O default 10.000 só é liberado para novas execuções após ambos os gates: benchmark local aprovado e probe real aprovada. Nesta execução, a probe validou o máximo de 10.000, mas o benchmark mediu vazão inferior ao perfil de 5.000; por isso 5.000 ficou como default. A coleta ativa e revisões antigas permanecem intocadas.

## Critérios de aceite

- **AC1 — limites da fonte separados:** SPEC/plan citam fontes e diferenciam 10.000 por página, ausência de teto total encontrado, contagem hits.total até 10.000 e cota de 120 requests/minuto.
- **AC2 — contrato page_size:** testes de validação aceitam 10 e 10.000, rejeitam 9 e 10.001; entradas manuais, grupos, refresh, busca salva/agendada, API/OpenAPI e Radar compartilham default 5.000 selecionado pelo benchmark e a mesma faixa.
- **AC3 — total maior que o teto histórico:** teste PostgreSQL com pelo menos 25.001 hits completa a query por várias páginas até a página vazia; hits.total com relation gte não interrompe a sequência.
- **AC4 — exaustão correta:** página curta continua; somente hits vazio confirma fim. Resposta inválida, falha upstream ou cursor repetido não vira conclusão vazia.
- **AC5 — limite manual opcional:** ausência/nulo permite continuar sem teto; limite explícito é cumulativo por tribunal, cria parcial/limit, e pode ser limpo ou aumentado em continuação preservando cursor e contadores. Quando o saldo for menor que 10, a request continua respeitando o mínimo da fonte e só o saldo é persistido.
- **AC6 — recuperação/idempotência:** falha no fetch ou persistência não avança checkpoint da página; replay e reinício do worker não duplicam efeitos; cancelamento preserva páginas já confirmadas.
- **AC7 — cota global:** testes com relógio controlado e pelo menos dois consumidores/tribunais provam máximo de 120 tentativas em qualquer janela móvel de 60 segundos, intervalo mínimo, inclusão de retries, gate antes da lease e respeito ao cooldown/Retry-After.
- **AC8 — isolamento/fairness:** jobs dos quatro presets e tribunais permanecem independentes; limitador comum não cria starvation ou mistura de checkpoints/resultados.
- **AC9 — benchmark local:** relatório contém três perfis, três repetições e métricas desta SPEC; perfis passam integridade, tempo/lease e memória declarada. O default é o perfil íntegro de maior vazão média. Se 10.000 não alcançar o perfil de 5.000, registrar a decisão e manter 10.000 como máximo explícito.
- **AC10 — probe externa:** relatório limitado confirma resposta de 10.000 e avanço para próxima página ou classifica explicitamente falha/inconclusivo. Não declara disponibilidade nacional.

## Plano de testes e evidências

- Testes unitários para faixas/defaults, cálculo do tamanho efetivo, budget cumulativo opcional, parsing de relation gte, cursor completo e validação de avanço.
- Testes PostgreSQL concorrentes para reserva global, janelas móveis, pre-lease gate, fairness, cooldown compartilhado, retomada, rollback de página, checkpoint CAS e replay idempotente.
- Teste do worker interrompido antes e depois do commit com 10.000 registros por página, comparando contagens, digests, cursor e revisão à execução contínua.
- Integração com HTTP simulado cobrindo respostas de 10.000 hits, segunda página via search_after, página curta, página vazia, 429/Retry-After, timeout, 5xx, envelope inválido e cursor repetido.
- OpenAPI e tipos gerados atualizados; frontend mantém os estados de progresso, parcial, erro e cancelamento sem apresentar o teto manual como limite obrigatório.
- Probe real e benchmark local permanecem evidências separadas. Fixture prova contrato local, não capacidade ou disponibilidade do DataJud.

## Exclusões

- Mudar o intervalo de janela manual, critérios dos quatro presets, allowlist ou política de dados.
- Aumentar concorrência remota ou exceder a cota oficial.
- Prometer cobertura total, snapshot consistente ou disponibilidade nacional.
- Alterar qualquer execução operacional, dados, chave, banco ou checkpoint durante a revisão da SPEC.

## Evidências e resultado

### Benchmark local

Executado em 13/10/2026 no Compose de teste, com PostgreSQL isolado, 2 GiB de memória para o container worker, três repetições por perfil, 25.001 capas sintéticas únicas e movimentos vazios. Cada execução chegou à página vazia após páginas cheias e última página curta; total `gte` ficou em 10.000. Não houve retry, falha, OOM, timeout nem erro de posse. Parsing/validação consumiram cerca de 0,4 s por perfil; ingestão/checkpoint domina o tempo medido.

| page_size | Requests/run | Bytes simulados/run | Vazão média (hits/min) | p50/p95 média (ms) | Parse/validação média (s) | Ingestão/checkpoint média (s) | Pico RSS máximo (bytes) |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1.000 | 27 | 6.126.502 | 8.496 | 13,10 / 20,04 | 0,420 | 175,71 | 106.541.056 |
| 5.000 | 7 | 6.125.302 | 8.555 | 69,73 / 100,87 | 0,423 | 174,76 | 124.882.944 |
| 10.000 | 4 | 6.125.122 | 7.837 | 112,25 / 148,34 | 0,411 | 191,32 | 143.708.160 |

O perfil 10.000 completou íntegro e ficou abaixo do envelope de memória, mas seu gate de default **falhou por vazão**: 7.837 hits/min contra 8.555 em 5.000 (aproximadamente 8,4% menor). O tempo de ingestão/checkpoint também subiu para 191,32 s, enquanto parsing permaneceu em torno de 0,4 s. Portanto, 5.000 é o default de novas execuções; 10.000 segue como máximo explícito, sem alegação de ser mais rápido.

Relatório reproduzível: [`docs/evidence/SPEC-026-benchmark-2026-10-13.json`](../docs/evidence/SPEC-026-benchmark-2026-10-13.json). O relatório conserva métricas de cada uma das nove execuções e o envelope de memória medido.

### Probe DataJud limitada

Executada em 13/10/2026 após confirmação do operador sobre a revisão dos termos vigentes e preflight somente leitura da fila real. A consulta usou TJGO, preset `execucoes.amplas`, ajuizamentos de 01/05/2026 a 31/05/2026, sort congelado `@timestamp` + `id.keyword`, tamanho 10.000 e zero retry.

- Página 1: HTTP 200, 10.000 hits, `hits.total` 10.000 `gte`, 57.069 ms e 51.577.493 bytes.
- Página 2 por `search_after`: HTTP 200, 2.681 hits, total `gte`, 28.728 ms e 15.686.569 bytes; o cursor avançou.
- Total: duas requests sequenciais, estado `VALIDATED`, sem escrita na base de produto.

Essa observação valida apenas o endpoint TJGO, o preset e a janela usados naquele momento. `execucoes.amplas` procura classes gerais de execução e não confirma vínculo rural nem disponibilidade nos demais tribunais.

Relatório sanitizado: [`docs/evidence/SPEC-026-datajud-probe-2026-10-13.json`](../docs/evidence/SPEC-026-datajud-probe-2026-10-13.json). Ele não contém chave nem payload bruto dos processos.

### Validações locais e conclusão

Validações executadas em ambiente local isolado, sem uso do banco operacional:

- Backend/API: suíte completa em PostgreSQL de teste isolado, com limite de memória de 2 GiB; **396 testes passaram** em 211,69 s. O caso de budget menor que 10 validou request com size mínimo 10, persistência limitada ao saldo e cursor no último hit gravado. Houve um aviso de depreciação do `TestClient` do Starlette, sem falha.
- Backend estático: `ruff check src tests` passou; `ruff format --check src tests` confirmou 115 arquivos formatados; `mypy src` passou em 81 arquivos.
- Frontend: `npm run test` passou em 14 arquivos / 69 testes; `npm run lint`, `npm run typecheck`, `npm run build` e `npm run openapi:check` passaram. O schema OpenAPI e os tipos TypeScript gerados estão atualizados.
- Grafo: `graphify update .` concluiu e atualizou o grafo local.
- Revisão: diff e arquivos staged foram inspecionados; a SPEC, o índice e o plano registram os resultados, e a implementação foi commitada.

Os critérios AC1–AC8 passaram nos testes de contrato, coleta, persistência, limite compartilhado, recuperação e integração frontend/API. AC9 aprovou a escolha do perfil íntegro de maior vazão média: 5.000 hits/página; o perfil de 10.000 permanece suportado como máximo, mas não foi promovido a default porque sua vazão foi inferior. AC10 foi validado pela probe TJGO limitada a duas páginas. A probe não afirma disponibilidade nacional, completude jurídica ou vínculo rural.
