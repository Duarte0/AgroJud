# SPEC-025 — Suporte multitribunal no produto

Status: DONE — implementação e critérios AC1–AC13 validados localmente em 12/10/2026; rollout real permanece desativado por padrão.

Data: 12/10/2026

Milestone: Pós-M9 / expansão controlada

Dependências: [SPEC-002 — Contratos e adaptadores de fonte](SPEC-002-contratos-e-adaptadores-de-fonte.md), [SPEC-004 — Identidade e reconciliação](SPEC-004-identidade-e-reconciliacao.md), [SPEC-005 — Persistência de capas e payloads](SPEC-005-persistencia-de-capas-e-payloads.md), [SPEC-006 — Movimentos e quarentena](SPEC-006-movimentos-e-quarentena.md), [SPEC-007 — Jobs, leases e posse](SPEC-007-jobs-leases-e-posse.md), [SPEC-008 — Paginação e checkpoints](SPEC-008-paginacao-e-checkpoints.md), [SPEC-009 — Retries e recuperação](SPEC-009-retries-e-recuperacao.md), [SPEC-010 — Catálogo temático versionado](SPEC-010-catalogo-tematico-versionado.md), [SPEC-011 — API operacional e OpenAPI](SPEC-011-api-operacional-e-openapi.md), [SPEC-012 — Frontend de coleta e consulta](SPEC-012-frontend-de-coleta-e-consulta.md), [SPEC-015 — Acompanhamento manual](SPEC-015-acompanhamento-manual.md), [SPEC-016 — Referência histórica e novidades](SPEC-016-referencia-historica-e-novidades.md), [SPEC-017 — Buscas salvas e agendamento](SPEC-017-buscas-salvas-e-agendamento.md), [SPEC-018 — Indicadores da base local](SPEC-018-indicadores-da-base-local.md), [SPEC-019 — Exportação CSV](SPEC-019-exportacao-csv.md) e [SPEC-024 — Validação multitribunal do DataJud](SPEC-024-validacao-multitribunal-datajud.md).

## Objetivo

Permitir que a pessoa usuária selecione um ou mais tribunais validados para uma busca, acompanhe resultado e execução separadamente por tribunal e consulte uma conclusão parcial sem perder dados, progresso ou checkpoints dos demais. Aplicar a mesma regra a buscas manuais, salvas/agendadas e atualizações de acompanhamentos, mantendo filtros, indicadores e exportações coerentes com a origem escolhida.

## Contexto e auditoria do acoplamento atual

O esquema já representa a origem de forma reutilizável: `Representation` tem tribunal e identidade própria, o processo continua deduplicado pelo CNJ, `Job` guarda tribunal, e collection/checkpoint pertencem ao job. Esse desenho deve ser preservado. O worker e os contratos de consulta, porém, ainda executam TJGO como caso fixo; os fluxos de produto também assumem uma única coleta/job.

| Local atual | Acoplamento observado | Mudança desta SPEC |
| --- | --- | --- |
| `backend/src/agrojud/sources/contracts.py` | `SourceQuery` assume TJGO, `NormalizedSourceCover` é `Literal["TJGO"]`, e a consulta compilada e o filtro de payload fixam a mesma origem. | Tornar tribunal explícito no contrato normalizado e validar aliases/siglas pela allowlist. |
| `backend/src/agrojud/sources/datajud.py` e `factory.py` | Endpoint real constante para TJGO e factory sem parâmetro de tribunal. | Resolver endpoint por alias validado da allowlist versionada; manter uma implementação DataJud e o mesmo transporte. |
| `backend/src/agrojud/sources/catalog.py` e `thematic_catalog.v1.json` | Catálogo e metadados do catálogo identificam TJGO; a compilação dos presets parte desse padrão. | Compartilhar os mesmos presets e códigos TPU; aplicar o gate S5 por par tribunal/preset, sem clonar catálogo ou regras. |
| `backend/src/agrojud/services/collection.py` | Snapshot de consulta sem tribunal é convertido para TJGO. | Manter esse default somente na leitura de snapshot/API legada; toda nova coleta registra tribunal canônico explicitamente. |
| `backend/src/agrojud/api/watchlist.py` | Atualização manual consulta CNJ usando o default TJGO e seleciona refreshes recentes com filtro TJGO. | Criar um grupo com membros por tribunal de representação conhecida e capacidade validada; ler os resultados por membro. |
| `backend/src/agrojud/services/scheduling.py` e `saved_searches.py` | Uma versão de busca não persiste seleção de tribunais; cada disparo cria um job e `ScheduleDispatch` referencia um `job_id`. | Persistir a lista na revisão imutável e associar dispatches novos ao grupo, preservando leituras antigas. |
| `backend/src/agrojud/api/process_filters.py` | Filtros comuns de Processos não incluem tribunal. | Adicionar filtro multi-valor por origem sem multiplicar processos via join. |
| `backend/src/agrojud/api/overview.py` e `services/exports.py` | Indicadores e CSV usam os filtros comuns, mas não restringem as representações agregadas por tribunal. | Aplicar a seleção aos dados de representação; manter contagem de processos distintos por CNJ. |
| `frontend/src/pages/radar-page.tsx` e `overview-page.tsx` | Texto, placeholder e universo da tela citam TJGO; a busca não oferece seleção de tribunal. | Apresentar seleção multi-tribunal informada pela API e mostrar cobertura e conclusão parcial por tribunal. |

Não substituir a identidade atual de processo/representação, a lógica de ingestão, paginação, retry, posse ou checkpoint. Um membro do grupo reutiliza a coleta e o job já existentes para um tribunal. O grupo só agrega a solicitação, a cobertura e os estados dos membros.

## Gate de evidência e disponibilidade inicial

A única fonte de habilitação nesta entrega é a matriz revisada da SPEC-024: `docs/evidence/datajud-multitribunal-2026-10-11-lote-01/matrix-merged-after-tjms-rerun-reviewed.json`. A revisão tem `coverage_status=APPROVED_PARTIAL`, `coverage_assessment=EVIDENCE_INCOMPLETE`, decisão `APPROVED`, estado `REVIEWED`, justificativa limitada às evidências `VALIDATED` e data `2026-10-12T02:00:17.360996+00:00`. Seu `matrix_review.matrix_sha256` é `a19aa622b10a3001b46596f1b2faabe53bd5e912808e2c27b6a42a868032aa13`; o SHA-256 do arquivo revisado é `c9d43ac5879acf4fde6d2a99d3485b96dba73af54e06ebe4db746a8d3fb42b8d`. Versões registradas: allowlist `2026-10-11`, catálogo `1.1.0`, quatro presets `1.0.0` e TPU `06/10/2026`.

Na matriz, S1/S2 completo está `VALIDATED` para TJGO, TJMT, TJMS, TJPR e TRF1; os dez estados S1/S2 de cada um desses cinco também estão validados. Os demais 28 tribunais estão `NOT_RUN`. S5 tem 16 pares `VALIDATED`, quatro `INCONCLUSIVE` e 112 `NOT_RUN`:

| Preset | Tribunais inicialmente habilitáveis |
| --- | --- |
| `execucoes.amplas` | TJGO, TJMT, TJMS, TJPR, TRF1 |
| `recuperacao_judicial.por_assunto` | TJMT, TJMS, TJPR |
| `recuperacao_judicial.por_classe` | TJGO, TJMT, TJMS, TJPR |
| `rural.credito_contratos` | TJGO, TJMT, TJMS, TJPR |

TRF1 permanece indisponível nos três presets em que S5 está `INCONCLUSIVE`; TJGO permanece indisponível para `recuperacao_judicial.por_assunto`, que a matriz atual classifica como `INCONCLUSIVE`. Uma aprovação histórica em outro documento, arquitetura compartilhada, endpoint existente ou resposta sem resultados não altera esse estado.

Uma combinação só pode executar quando a matriz revisada atual estiver carregada e íntegra, a revisão continuar `APPROVED`/`REVIEWED`, S1/S2 estiver completo `VALIDATED` para o tribunal e, quando houver preset, o par S5 exato estiver `VALIDATED` para as versões atuais de allowlist, catálogo, preset e TPU. Matriz ausente, hash divergente, revisão rejeitada, versão divergente ou estado `INCONCLUSIVE`, `INCOMPATIBLE`, `NOT_RUN`/`NOT_APPLICABLE` falha fechado e não envia HTTP. Atualizar evidências não habilita automaticamente a aplicação: matriz nova exige revisão e promoção explícita de configuração.

## Decisões

- **Rollout progressivo por capability:** publicar a estrutura e os fluxos com a nova capacidade desligada; habilitar por configuração o arquivo revisado e somente os pares da tabela acima. O primeiro rollout cobre cinco tribunais, mas a disponibilidade de S5 varia por preset. Não ativar cobertura nacional nem seleção de tribunais sem evidência.
- **Gate aplicado também à seleção unitária nova:** após ativar o modo, todo novo job DataJud passa pelo gate, inclusive consultas de um tribunal. Revisões antigas sem tribunal são lidas como TJGO, mas uma combinação TJGO/preset inconclusiva fica bloqueada para novas chamadas. Os dados TJGO já persistidos permanecem íntegros e consultáveis.
- **Um grupo, jobs filhos existentes:** o grupo não é um job de coleta e não faz chamadas externas. Cada tribunal elegível recebe seu próprio `Job`, `Collection`, lease, tentativas, cursor e checkpoint atuais. A lógica do worker segue única.
- **Origem canônica e allowlist:** UI/API usam sigla canônica, como `TJMS`; a resolução DataJud usa alias validado, como `tjms`. Nunca aceitar endpoint ou URL fornecidos pela pessoa usuária.
- **Sem reescrita de história:** não converter registros, collections, jobs, checkpoints ou versões antigas. Compatibilidade é feita na leitura e em novas revisões.
- **Filtros por existência de representação:** selecionar tribunais inclui um processo se houver ao menos uma representação naquela seleção. Indicadores por origem e exportação usam apenas representações correspondentes; o total de processos continua deduplicado por CNJ. Essa regra evita inflação por joins e mistura de origens fora do filtro.
- **Catálogo único:** consulta, classes/assuntos e regras seguem a implementação existente. A matriz autoriza combinações; não cria catálogo por tribunal nem infere validade pela presença de códigos TPU.

## Escopo

1. Generalizar o contrato e a resolução de endpoint DataJud a partir da allowlist da SPEC-024, preservando transporte, parsing, erros tipados, sanitização e seleção explícita de ambiente real/demo.
2. Tornar tribunal parte obrigatória do snapshot de toda nova consulta/collection. O default TJGO fica restrito a requests e snapshots antigos.
3. Adicionar leitura versionada da matriz revisada e uma resposta de disponibilidade por tribunal, capacidade e preset. Não executar probe/merge/review junto à API ou worker de produto.
4. Persistir grupos de execução e membros por tribunal; enfileirar um job/collection normal para cada membro elegível e manter o estado de membro inelegível sem abrir job externo.
5. Incluir seleção de tribunais em buscas manuais, buscas salvas e novas revisões, dispatches agendados e atualização manual/agendada de acompanhamentos.
6. Aplicar a seleção de tribunal nos filtros de processos, indicadores, exportação, detalhe/status de grupos e UI.
7. Atualizar OpenAPI e tipos TypeScript gerados, migrations aditivas, testes de contrato e documentação de rollout.

## Contratos e comportamento

### Consulta e disponibilidade

- `tribunal` é sigla canônica em consulta/snapshot; a validação rejeita valor desconhecido, alias ambíguo e repetição. Consulta legada sem campo recebe TJGO somente na fronteira compatível.
- A nova leitura de disponibilidade informa versão/hash da matriz, estado S1/S2 e, por preset, `VALIDATED`, `INCONCLUSIVE`, `NOT_RUN`, `INCOMPATIBLE`, `NOT_APPLICABLE` ou obsoleto, com razão sanitizada e habilitação efetiva. A UI não calcula a aprovação a partir de nomes de tribunais.
- O resolver só constrói URL para alias allowlisted. A validação de retorno exige `_source.tribunal` canônico igual ao alvo. Incompatibilidade ou origem divergente é erro da coleta, nunca fallback para TJGO.
- A consulta do preset usa o compilador atual. O tribunal selecionado entra no filtro de origem e nos metadados do snapshot; os códigos e a regra S5 permanecem os mesmos.

### Grupo e worker

- `ExecutionGroup` guarda id, tipo/origem do pedido, snapshot imutável de filtros e versão/hash da matriz, timestamps e estado agregado. `ExecutionGroupMember` tem chave única `(group_id, tribunal)`, estado/razão da disponibilidade, `job_id` opcional e snapshot dos contadores/cobertura necessários para consulta. O membro pode existir sem job quando a capacidade não estiver habilitada.
- Criar grupo, membros e jobs elegíveis na mesma transação curta, com as chaves de idempotência já usadas pelos serviços do tipo de pedido. Nenhuma chamada HTTP ocorre nessa transação. A migration adiciona as tabelas e relacionamentos sem alterar linhas antigas.
- Um job filho mantém seu próprio `Job.tribunal`, collection, retry budget, lease, página e checkpoint. O worker recebe jobs existentes pelo mecanismo da fila; não há `MultiTribunalWorker`, cursor compartilhado ou transação abrangendo tribunais.
- Falha, timeout, cursor inválido, rejeição ou esgotamento de retry de um membro termina/pausa somente esse job e preserva páginas confirmadas. Outros membros elegíveis seguem pela fila. Limite global de autenticação/rate limit continua respeitando o cooldown existente; membros não iniciados ficam `NOT_RUN`/bloqueados com motivo. Nenhum erro vira resultado vazio.
- O membro apresenta status do job, cobertura, contadores de documentos/processos daquela origem, último checkpoint confirmado, motivo de falha e horários. Percentual global de páginas não é inventado; o grupo mostra quantos membros terminaram e o progresso individual de cada um.
- Estados de membro incluem `QUEUED`, `RUNNING`, `RETRY_WAIT`, `COMPLETED`, `PARTIAL`, `FAILED`, `CANCELLED`, `BLOCKED_NOT_VALIDATED` e `NOT_RUN`. O grupo fica `QUEUED` quando há job na fila e nenhum em execução/espera de retry, e `RUNNING` enquanto houver job em execução ou aguardando retry. Nos estados terminais: `COMPLETED` se todos os membros concluem sem pendência, inclusive quando uma consulta válida retorna zero hits; `PARTIAL` se algum membro em si for parcial ou houver membro concluído e outro falho/bloqueado/cancelado/não executado; `FAILED` se houve falha de membro executável e nenhum concluiu/ficou parcial; `BLOCKED` se nenhum membro pôde executar; `CANCELLED` se todos os membros executáveis foram cancelados sem resultado concluído/parcial. Preservar também o estado por membro na API.
- Resultados são gravados pela ingestão existente. O grupo relaciona cada resultado à origem via member → job → collection → representation; não mantém cópia paralela de capas ou eventos. CNJ continua deduplicando `Process`, enquanto uma representação por tribunal é preservada.

### API e buscas salvas/agendadas

- Expor `GET /api/v1/tribunal-availability?preset_id=...` para opções e razões por preset; `POST /api/v1/job-groups` para criar uma execução com `kind`, `criteria` e `tribunals`; e `GET /api/v1/job-groups/{group_id}` para progresso, membros, cobertura e resultados. O retorno de criação inclui `group_id`, snapshot/hash da matriz e uma entrada por tribunal. O parâmetro `preset_id` é opcional para consulta direta por CNJ, que depende apenas de S1/S2.
- Requests manuais com tribunal/preset indisponível falham antes de criar trabalho, com erro tipado e estado atual da matriz. Valor desconhecido retorna validação 4xx. Requests legados sem campo resolvem TJGO.
- Uma revisão nova de `SavedSearchVersion` grava a seleção explícita em `filters.tribunals`. Revisões antigas sem essa chave leem como `['TJGO']`; não alterar seus bytes, versão ou histórico. Editar seleção cria revisão imutável, como os demais critérios.
- Disparo manual/agendado de uma revisão grava um grupo com seu snapshot. Se uma combinação armazenada ficou sem validação desde sua criação, a dispatch cria membro `BLOCKED_NOT_VALIDATED` e não abre job para essa combinação; outros membros ainda elegíveis podem prosseguir. O grupo revela a lacuna.
- Acrescentar referência opcional ao grupo em `ScheduleDispatch`, mantendo `job_id` e linhas existentes. Novos dispatches usam a mesma chave idempotente de alvo/data e apontam a um grupo; constraint garante exatamente uma referência válida (job legado ou grupo novo).
- Respostas existentes de job continuam válidas. Rotas existentes que recebem/retornam um único `job_id` permanecem legíveis para os clientes e registros antigos; os novos fluxos de grupo retornam também `group_id` e filhos, sem reinterpretar um job antigo como se já tivesse pertencido a um grupo.

### Acompanhamentos

- Atualização de item acompanhado procura cada tribunal distinto já presente nas representações do processo e cria um membro independente para cada origem com S1/S2 válido; usa consulta exata ao CNJ, sem S5, pois não é busca temática.
- Representação antiga de origem sem validação fica `BLOCKED_NOT_VALIDATED`; não apagar a representação nem seu baseline. Se não houver representação elegível, registrar grupo bloqueado sem HTTP.
- Cada membro atualiza somente a representação, baseline e novidades de seu tribunal. Falha ou resposta incompleta mantém o baseline anterior e não apaga novidades nem modifica a classificação humana. Sucesso em outra origem não substitui essa referência.
- Atualizações agendadas mantêm deduplicação e idempotência atuais por alvo/período; cada alvo aponta a um grupo. Agendas são isoladas por item acompanhado, sem uma busca única compartilhada para vários CNJs.

### Processos, indicadores e exportação

- Adicionar filtro multi-tribunal aos filtros comuns de Processos. Implementar predicado por `EXISTS` sobre `Representation`, sem join que multiplique processos. A lista conserva um processo por CNJ; suas representações visíveis correspondem à seleção aplicada.
- Overview aplica o mesmo filtro: contagem de processos distintos por CNJ e contagem/agrupamento de representações apenas dos tribunais selecionados. Todos os indicadores e drill-downs recebem o filtro.
- CSV preserva o formato seguro, limite e snapshot da SPEC-019. Cada processo sai uma vez; colunas e agregações de origem incluem somente as representações do filtro. Sem filtro, mantém comportamento inclusivo atual e lista as origens disponíveis.
- Status de coleta/grupo permite filtrar e reconhecer origem e conclusão parcial. A cobertura de coleta e a cobertura da matriz são distintas: ausência de registros locais não significa fonte vazia nem par validado.

### Frontend

- Radar e formulário de busca exibem seleção multi-valor de tribunais carregada da API. Selecionar preset atualiza a disponibilidade em linha. Por padrão, marcar todos os tribunais habilitados para aquele preset; a pessoa pode retirar um ou mais. Opções não validadas ficam desabilitadas e mostram estado/razão, sem sugerir que retornaram zero processos.
- Consultas diretas por CNJ usam o gate S1/S2. Busca por preset usa S1/S2 e S5 para cada par selecionado. Validação API permanece autoridade final.
- A tela de execução lista um cartão/linha por tribunal, com progresso, resultados, estado, checkpoint e motivo. Mostra `concluída parcialmente` quando apropriado e mantém acesso ao resultado dos membros concluídos enquanto outros seguem ou falham.
- Busca salva permite escolher e revisar os tribunais; telas de agendas e acompanhamentos mostram o grupo mais recente e resultado individual por origem. Processos, filtros, indicadores e exportação compartilham o mesmo filtro de tribunal.
- Substituir textos e placeholders que afirmam TJGO como universo fixo; preservar nome do produto, ambiente real/demo, acessibilidade, paginação e links de evidência.

## Persistência e compatibilidade

- Migration incremental e aditiva para grupo/membros e vínculo de dispatch. Não renumerar, converter ou apagar `Job`, `Collection`, `JobCheckpoint`, `Representation`, payloads, observações, triagem, baselines ou novidades existentes.
- Manter chave natural existente de `Representation(source, tribunal, source_id)` e chave CNJ de `Process`; uma nova origem gera representação, não um segundo processo.
- Nenhum backfill operacional obrigatório. Campo `filters.tribunals` ausente em versão histórica é interpretado como TJGO. Requests de API antigas sem campo também mantêm TJGO. Snapshot novo sempre persiste a lista para reexecução determinística.
- Manter filtro de leitura por `Job.tribunal`; consultas históricas e links para detalhe de job/collection continuam funcionando. As rotas de processo retornam origens já existentes sem remodelar dados.
- Índices adicionais só para joins/consulta de grupo comprovados no plano real de queries e cobertos por migration; constraints protegem unicidade de membro e referência de dispatch.
- Migration deve aplicar em banco vazio e nas revisões anteriores suportadas, com dados TJGO existentes. Não usar reset de banco/volumes como validação.

## Rollout

1. Implementar contrato, leitura do gate, schema aditivo e compatibilidade legada; manter a flag multitribunal desligada. Validar migration e fluxos locais com HTTP simulado.
2. Promover explicitamente o arquivo revisado da SPEC-024 e sua soma de verificação para a configuração real; conferir versões da allowlist, catálogo, presets e TPU. Se não coincidir, manter a função nova desabilitada.
3. Ativar a flag somente para os pares listados na tabela de disponibilidade inicial. A UI pré-seleciona todos os tribunais habilitados do preset. A seleção de um único tribunal usa o mesmo fluxo com um membro.
4. Observar estados por tribunal, falhas, bloqueios por gate, cooldowns e conclusão parcial em logs/indicadores existentes, sem gravar payload, credencial, CNJ ou cursor sensível em log. Reverter a flag interrompe novas admissões; jobs confirmados e seus dados/checkpoints permanecem íntegros e podem ser lidos/retomados conforme seu estado.
5. Para ampliar tribunais/presets, executar o processo externo limitado da SPEC-024, revisar a nova matriz e promover explicitamente a revisão. Não exigir alteração de código por cada tribunal validado quando alias e contrato já estiverem cobertos.

Após ativar a flag, o gate se aplica a todas as novas coletas DataJud, inclusive TJGO unitário. Revisões legadas sem tribunal continuam resolvendo para TJGO; se o seu preset estiver inconclusivo na matriz vigente, a nova execução fica bloqueada e explica a lacuna. Dados e resultados locais históricos continuam disponíveis.

## Critérios de aceitação

- **AC1 — auditoria e contrato:** contratos/query/snapshot novos aceitam tribunais canônicos allowlisted; resolver URL não aceita entrada livre e valida a identidade retornada. Fluxos legados ausentes continuam usando TJGO.
- **AC2 — gate fechado:** a matriz tem de ser revisada e aprovada, hash e versões devem coincidir, todas as capabilities S1/S2 requeridas devem estar `VALIDATED` e presets devem ter S5 `VALIDATED`. Matriz ausente, inválida, obsoleta ou inconclusiva impede HTTP e expõe motivo.
- **AC3 — disponibilidade inicial:** a API/UI habilita exatamente as combinações da tabela desta SPEC; os 28 `NOT_RUN`, quatro S5 `INCONCLUSIVE` e qualquer tribunal não validado não são anunciados nem tratados como vazios. TJGO/`por_assunto` é bloqueado no estado atual da matriz.
- **AC4 — seleção:** busca aceita um ou vários tribunais elegíveis; seleção vazia/duplicada/unknown tem erro explícito. Preset ajusta lista habilitada; valor fornecido por cliente desatualizado é recusado ou registrado como membro bloqueado em execução persistida, sem consulta externa.
- **AC5 — grupo e progresso:** cada tribunal selecionado tem estado, resultado, contador, cobertura, checkpoint e falha próprios. Falha/restart de um membro não altera checkpoint, tentativas, resultado ou estado dos demais. Pelo menos um cenário comprova conclusão parcial e leitura dos resultados bem-sucedidos.
- **AC6 — atomicidade/recuperação:** jobs filhos são criados com grupo/membros atomicamente; chamada HTTP segue fora de transação; ingestão/checkpoint continua atômica sob lease e replay permanece idempotente em cada filho.
- **AC7 — buscas salvas/agendadas:** nova revisão salva tribunais; revisões antigas leem como TJGO sem reescrita. Dispatch diário/manual é idempotente e referencia grupo novo, enquanto dispatches `job_id` antigos continuam acessíveis. Membro que perdeu gate fica explicitamente bloqueado.
- **AC8 — acompanhamentos:** origem atualiza isoladamente por representação conhecida; falha/incompletude conserva baseline, novidades e triagem humana. Trabalho agendado e manual não duplicam grupo por alvo/período.
- **AC9 — filtro comum:** tribunal selecionado inclui processo com ao menos uma representação correspondente; contagem de CNJ é distinta. Indicadores, drill-down e CSV restringem representações ao filtro e mantêm processo uma vez.
- **AC10 — frontend e API:** seleção multi-tribunal, razões de indisponibilidade e status por membro são acessíveis e responsivos; OpenAPI e tipos gerados descrevem listas, grupo, membro, bloqueio e conclusão parcial. Sem regressão na jornada unitária TJGO elegível.
- **AC11 — banco compatível:** migration passa em vazio e em banco com dados TJGO das revisões suportadas; histórico de jobs, checkpoints, buscas, dispatches, representações e baselines não é alterado.
- **AC12 — cobertura dos testes:** testes locais e HTTP simulado demonstram gate, falhas independentes, retomada por checkpoint, idempotência, filtros/contagens sem duplicação, CSV, UI e compatibilidade legada. Nenhum fixture/teste local será apresentado como nova evidência real DataJud.
- **AC13 — operação:** flag desligada impede novo comportamento multitribunal; ativação exige matriz promovida, e rollback impede novas admissões sem apagar resultado confirmado. Sem endpoint de tribunal não validado.

## Testes necessários na implementação

- Unitários de allowlist, aliases/siglas, filtro de origem, query por tribunal, identidade `_source.tribunal`, compilação única dos presets e leitura/versionamento/hash do gate.
- Integração worker/PostgreSQL para duas ou mais origens, sucesso parcial, falhas HTTP diferentes, cooldown, cursor/checkpoint isolado, lease vencida, restart/replay e persistência atômica sem manter transação aberta durante HTTP.
- API para disponibilidade, seleção 1/N, seleção rejeitada, resposta por grupo/membro, revisão antiga sem tribunais, revisão nova imutável, dispatch repetido e vínculo legado por `job_id`.
- Acompanhamentos com mais de uma representação, falha numa origem e sucesso noutra, retenção de baseline, novidades, triagem e deduplicação manual/agendada.
- Consultas PostgreSQL com processos de várias representações para provar `EXISTS`, contagem CNJ sem inflação, filtro de métricas e linha CSV única com apenas origens selecionadas.
- Frontend para seleção por preset, default com todos os elegíveis, bloqueio com razão, seleção de um/vários, progresso individual, conclusão parcial, busca salva/agendada e filtro de tribunal no Radar/indicadores.
- Migrations em PostgreSQL isolado vazio e sobre a revisão anterior suportada com fixtures de dados TJGO. Gerar/validar OpenAPI e tipos frontend; executar suites, lint, typecheck, build e Playwright conforme AGENTS.md durante a implementação.

## Erros e casos-limite

- `401`, `403`, `429`, `5xx`, timeout, formato inválido, cursor repetido ou identidade divergente é falha/incompatibilidade do membro, nunca sucesso vazio. Aplicar as classificações e cooldowns atuais.
- Falha de um tribunal após páginas confirmadas mantém resultado parcial e checkpoint. Retomar só aquele job continua do seu cursor; reiniciar um membro não reseta outro.
- Resposta global de autenticação/rate limit respeita o limitador existente; membros ainda não iniciados ficam pendentes/bloqueados com causa, sem retry paralelo que ignore cooldown.
- Preset atualizado sem nova evidência S5 fica indisponível; troca de TPU, allowlist ou versão de matriz invalida o gate conforme dependência afetada e exige revisão/promoção.
- CNJ com representações duplicadas no mesmo tribunal gera uma operação por tribunal, não por linha repetida; identidades distintas dentro da origem permanecem conforme constraints atuais.
- Um processo incluído por mais de um tribunal aparece uma vez nos totais de processo; indicadores de representação e exportação obedecem exatamente ao filtro.
- Cancelamento do grupo interrompe os jobs filhos segundo as semânticas existentes; páginas confirmadas sobrevivem e estados individuais indicam cancelamento ou término anterior.

## Fora de escopo

- Rodar probes externas, revisar/aprovar automaticamente matriz, ampliar a allowlist além dos 33 alvos e tratar qualquer estado não validado como disponível.
- TRTs, TREs, tribunais superiores, Justiça militar, fontes diferentes do DataJud ou URLs definidas pelo cliente.
- Replicar catálogo/presets por tribunal, criar framework genérico de provedores/orquestração ou worker próprio multitribunal.
- Deduplicação de processo distinta do CNJ, reconciliação entre representações como se fossem uma origem única, mudança de regra de triagem ou inferência jurídica.
- WebSocket/SSE ou percentual global de páginas sem denominador confiável.
- Excluir, converter ou reprocessar os dados TJGO existentes durante a migration.

## Limites de evidência

A matriz revisada comprova apenas o estado externo observado e aprovado na revisão registrada. A implementação local, mocks, fixtures, healthcheck e resultado salvo não elevam um estado. Novos tribunais/presets permanecem bloqueados até nova matriz validada e revisada conforme SPEC-024. O aceite de software não declara cobertura nacional, atualidade ou completude jurídica.

## Evidências da implementação

Os critérios foram verificados com PostgreSQL descartável e isolado, testes HTTP simulados e respostas de API interceptadas no navegador. Nenhuma probe DataJud externa foi executada.

| Critérios | Evidência local |
| --- | --- |
| AC1–AC4 | `backend/tests/test_multitribunal_contracts.py` e `test_multitribunal_product.py`: allowlist/aliases, tribunal no payload e identidade, hash/versões, gate fail-closed, combinações aprovadas e rejeição antes de criar jobs. |
| AC5–AC6 | `test_group_children_keep_independent_jobs_coverage_and_checkpoints_after_partial_failure`: jobs e checkpoints independentes, retomada/restart de um membro, estado parcial e atomicidade. |
| AC7 | `test_saved_revision_and_daily_dispatch_preserve_group_snapshots_and_coalesce_active_target` e compatibilidade de dispatch legado por `job_id`. |
| AC8 | `test_watch_refresh_reuses_active_group_and_deduplicates_same_tribunal_representations`: seleção por representação, deduplicação e reuso do grupo ativo. |
| AC9 | Testes de lista, indicadores, drill-down e CSV por tribunal em `test_multitribunal_product.py` e `test_exports.py`, com contagens distintas por CNJ. |
| AC10 | 65 testes frontend, `frontend/e2e/multitribunal.spec.ts` no Chromium a 390×844 com API simulada, verificação de seleção/bloqueio, envio do grupo, progresso parcial e ausência de rolagem horizontal; OpenAPI e tipos gerados conferidos. |
| AC11 | Testes de migration em banco vazio e a partir de revisões anteriores suportadas, incluindo preservação de processos/baselines TJGO; 364 testes backend passaram. |
| AC12–AC13 | Suítes locais demonstram compatibilidade, idempotência, flag desabilitada por padrão e gate de matriz/hash; nenhuma URL fora da allowlist é construída ou chamada. |

Validações executadas:

- `uv run --project backend --locked --group dev pytest backend/tests` — **364 passed**.
- `mypy --package agrojud`, `ruff check src tests`, `ruff format --check src tests` e `python -m compileall -q src tests` no diretório `backend` — aprovados.
- `npm run typecheck`, `npm run lint`, `npm run openapi:check`, `npm run test -- --run` (**14 arquivos, 65 testes**) e `npm run build` no diretório `frontend` — aprovados.
- `AGROJUD_API_URL=http://127.0.0.1:19999 npm run test:e2e -- e2e/multitribunal.spec.ts` — **1 passed**; a rota foi integralmente interceptada com respostas sintéticas.
- `docker compose --env-file .env.real.example --profile worker config --quiet` e o equivalente com `.env.demo.example` — configuração aprovada para os dois ambientes; a flag permanece `false` no exemplo real.

A configuração `DATAJUD_MULTITRIBUNAL_ENABLED` continua `false` por padrão e é passada ao API e worker pelo Compose. O Compose monta somente a matriz revisada como arquivo somente leitura; `DATAJUD_MULTITRIBUNAL_MATRIX_HOST_PATH` escolhe o arquivo do host, e `DATAJUD_MULTITRIBUNAL_MATRIX_PATH`/`DATAJUD_MULTITRIBUNAL_MATRIX_SHA256` identificam caminho e hash dentro do container. A ativação exige promover explicitamente caminho, SHA-256 e flag para uma matriz compatível com as versões atuais. A cobertura externa segue parcial para cinco tribunais; 28 continuam `NOT_RUN`, quatro pares S5 `INCONCLUSIVE`, e estes estados permanecem indisponíveis. Estes testes não são aceite operacional nem evidência externa nova.
