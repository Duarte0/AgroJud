# SPEC-024 — Validação multitribunal do DataJud (TJs e TRFs)

Status: DONE — implementação local; execuções externas e aprovação humana da matriz permanecem pendentes.

Milestone/Spike: S6 — validação externa pré-expansão

## Dependências

- [SPEC-002](SPEC-002-contratos-e-adaptadores-de-fonte.md) — transporte de tentativa única, contrato de consulta e normalização.
- [SPEC-003](SPEC-003-validacao-datajud-tjgo.md) — método, limites e classificação S1/S2. Sua evidência é específica do TJGO e não se transfere a outro tribunal.
- [SPEC-010](SPEC-010-catalogo-tematico-versionado.md) — catálogo TPU, presets ativos e método/limites de S5. Sua evidência é específica do TJGO e não se transfere a outro tribunal.

Referências oficiais consultadas em 11/10/2026: [catálogo de endpoints da API Pública DataJud](https://datajud-wiki.cnj.jus.br/api-publica/endpoints/), [TPU/SGT do CNJ](https://www.cnj.jus.br/tabela-processuais-unificadas/) e [Portaria CNJ nº 160/2020, texto compilado](https://atos.cnj.jus.br/atos/detalhar/3453), alterada pela [Portaria nº 374/2026](https://atos.cnj.jus.br/atos/detalhar/6972). Antes de cada rodada externa, confirmar novamente endpoint, versão taxonômica e regras vigentes. Não copiar chave pública ou credenciais para código, documentação ou evidências.

## Objetivo

Produzir evidência limitada, reproduzível e separada por tribunal para decidir onde o AgroJud poderá buscar processos na API Pública DataJud. O alvo desta validação é o conjunto de 33 endpoints de Justiça Estadual e Federal: 26 tribunais estaduais, TJDFT e seis TRFs.

A validação cobre S1/S2 (contrato, consulta e paginação) por endpoint e S5 (aplicabilidade e consultas dos quatro presets de descoberta) por par tribunal/preset. Ela prepara uma decisão futura de expansão; não habilita a coleta nem altera a disponibilidade atual do produto.

## Contexto e decisões

- A aplicação e as evidências atuais aprovam somente o TJGO. Não inferir suporte nacional a partir da arquitetura comum da API ou da semelhança dos endpoints.
- A rodada será dividida em lotes explícitos de no máximo cinco tribunais. Cada tribunal recebe relatório independente; o lote recebe um resumo que preserva resultados parciais.
- Antes de qualquer chamada S5, classificar cada par tribunal/preset com evidência oficial de TPU e competência. Par comprovadamente não aplicável recebe `NOT_APPLICABLE` e não gera chamada ao DataJud. Aplicabilidade sem evidência suficiente fica inconclusiva e também não gera chamada.
- Se não houver amostra S5, registrar `INCONCLUSIVE`. Uma nova tentativa exige que a pessoa operadora escolha outra janela de datas; não fazer varredura histórica automática.
- Evidência técnica do probe e decisão humana de habilitar uma capacidade são registros distintos. Nenhum resultado, nem mesmo `VALIDATED`, atualiza configuração ou habilita fonte automaticamente.
- A matriz por tribunal e preset será a entrada para uma SPEC posterior de expansão do produto. Essa SPEC posterior deverá cobrir busca manual, buscas salvas/agendadas e atualização de processos acompanhados; esta unidade não altera esses fluxos.
- Na futura expansão, a seleção inicial deverá incluir todos os tribunais validados e permitir restringir a seleção. Uma busca agrupada deverá criar trabalho, progresso e checkpoint independentes por tribunal, permitir conclusão parcial e expor quais tribunais falharam ou ficaram pendentes. Pares tribunal/preset não validados devem ser ignorados com lacuna de cobertura explícita, nunca apresentados como busca vazia bem-sucedida.

## Escopo

- Implementar ferramenta de validação multitribunal, testes do orçamento/sanitização e geração de relatórios por endpoint e por lote.
- Registrar uma allowlist versionada dos 33 aliases e resolver URLs somente a partir dela e do catálogo oficial consultado. A lista inicial é:
  - TJs: `tjac`, `tjal`, `tjam`, `tjap`, `tjba`, `tjce`, `tjdft`, `tjes`, `tjgo`, `tjma`, `tjmg`, `tjms`, `tjmt`, `tjpa`, `tjpb`, `tjpe`, `tjpi`, `tjpr`, `tjrj`, `tjrn`, `tjro`, `tjrr`, `tjrs`, `tjsc`, `tjse`, `tjsp`, `tjto`.
  - TRFs: `trf1`, `trf2`, `trf3`, `trf4`, `trf5`, `trf6`.
- Validar S1/S2 por tribunal e S5 para cada preset aplicável ao tribunal.
- Manter evidências e estados independentes por capability, tribunal e par tribunal/preset.

## Fora do escopo

- Mudar adaptadores, consultas, API, worker, frontend, banco, migrations ou configuração de produção para coletar de mais tribunais.
- Lançar busca nacional, consulta contínua, varredura histórica ou probes externas durante a implementação desta SPEC.
- Validar TRTs, TREs, tribunais superiores, tribunais militares, sistemas processuais próprios ou fontes externas ao DataJud.
- Criar novas taxonomias, presets, sinais ou regras. Sinais `sinal.*` não fazem parte da matriz S5 desta unidade.
- Declarar cobertura completa, atualidade ou integridade jurídica dos dados a partir de uma amostra limitada.

## Contrato da execução

### Lotes e isolamento

1. A pessoa operadora informa explicitamente de um a cinco aliases conhecidos. Alias fora da allowlist, repetido ou lote vazio falha antes de qualquer chamada.
2. Não selecionar automaticamente os 33 tribunais nem aceitar URL livre. O padrão existente TJGO permanece inalterado até decisão de implementação posterior.
3. Cada tribunal é processado isoladamente e resultados já obtidos são preservados. Falha, timeout, 403, 5xx ou resposta inválida em um endpoint não interrompe os demais do lote; não há retry automático. HTTP 401 ou 429 interrompe as chamadas restantes do lote, que ficam `NOT_RUN`, para evitar repetir uma falha de autenticação ou ultrapassar limite de acesso.
4. A execução real exige ambiente `real` e `DATAJUD_API_KEY` configurada no ambiente do backend. Se a chave estiver ausente ou o ambiente não for explicitamente real, o probe não envia HTTP.
5. O probe não abre conexão com o banco de produto e não lê nem grava jobs, processos, buscas salvas ou acompanhamentos.

### S1/S2 por tribunal

- Respeitar no máximo seis requisições DataJud por endpoint em cada execução, `size <= 100` por requisição e zero retry. Atingido o orçamento, qualquer capability não observada fica `INCONCLUSIVE` com motivo.
- Usar o transporte de tentativa única da SPEC-002 e os formatos já evidenciados na SPEC-003 como hipóteses iniciais, nunca como aprovação de outro tribunal.
- Observar e classificar separadamente: autenticação/resposta HTTP, envelope, shape dos campos essenciais, identidade do tribunal, filtro de data, busca exata por CNJ observado, identidade do documento de origem, sort `@timestamp`, candidato composto `@timestamp` + `id.keyword` quando aceito, e paginação.
- Só construir consulta exata com CNJ de um hit público observado durante a mesma execução. Nunca inventar número nem registrar o valor no relatório.
- Campo essencial: `_source` objeto; `numeroProcesso` textual com 20 dígitos; `tribunal` igual à sigla canônica do tribunal associada ao alias consultado pela allowlist (por exemplo, `tjgo` → `TJGO`); `_id` e `_source.id` textuais, não vazios e com a relação exigida pela SPEC-002 (`_id == _source.id`); `dataAjuizamento` presente e interpretável pelo contrato de normalização vigente. Divergência ou shape incompatível não recebe fallback silencioso.
- Validar paginação somente após observar duas páginas não vazias, cursor avançando, ordenação na fronteira e ausência de duplicação na amostra. Sort aceito pelo endpoint sem página seguinte não valida paginação. Registrar empates ausentes como limite da evidência.
- Classificar cada capability como `VALIDATED`, `INCOMPATIBLE`, `INCONCLUSIVE` ou `NOT_RUN`. Falta de hit, falha externa, ausência de exemplo ou esgotamento do orçamento não equivale a resultado vazio bem-sucedido.
- Cada tribunal tem resultado S1/S2 próprio. Sucesso de TJGO ou de qualquer endpoint vizinho não satisfaz a evidência de outro alias.

### Aplicabilidade e S5 por tribunal/preset

Os únicos presets nesta versão são `rural.credito_contratos`, `execucoes.amplas`, `recuperacao_judicial.por_classe` e `recuperacao_judicial.por_assunto`, respeitando a versão corrente do catálogo da SPEC-010.

1. Para cada um dos quatro presets em cada tribunal do lote, registrar a versão/data TPU e a referência oficial usada para classificar ramo de Justiça, competência e aplicabilidade dos códigos.
2. Estados de aplicabilidade: `APPLICABLE`, `NOT_APPLICABLE` ou `INCONCLUSIVE`. `NOT_APPLICABLE` exige evidência explícita de exclusão. `INCONCLUSIVE` exige justificativa e revisão, sem consulta DataJud. Só `APPLICABLE` pode prosseguir para S5.
3. Para cada par aplicável, respeitar janela de até 31 dias, no máximo duas requisições DataJud, tamanho de página até 100, intervalo de pelo menos um segundo entre chamadas do mesmo item e nenhum retry. Cada par tem seu próprio orçamento.
4. Preservar a classificação da SPEC-010: `query_status = VALIDATED` quando houver hits e todos os hits observados contiverem os códigos pedidos em cada dimensão filtrada; `INCOMPATIBLE` quando algum hit observado não satisfizer os códigos; `INCONCLUSIVE` sem hits ou sem resposta válida. `sample_status = VALIDATED` exige ao menos três documentos distintos com código estruturado correspondente; menos que três é `INCONCLUSIVE`, salvo evidência incompatível observada.
5. Código ausente em um documento não prova que o tribunal não tem processos daquele tema. Nunca converter zero hits, erro HTTP ou ausência de amostra em incompatibilidade ou sucesso vazio.
6. Se a janela não tiver amostra suficiente, gravar o intervalo testado e permitir nova execução apenas quando a pessoa operadora fornecer um novo intervalo de até 31 dias. Sem seleção manual, não experimentar meses ou anos anteriores.
7. O resultado S5 de TJGO serve como controle de regressão, mas continua vinculado ao TJGO e à versão específica do preset/catálogo testada.

## Modelo de evidência e relatórios

Cada relatório por tribunal inclui identificador da execução, data/hora, alias allowlisted, ambiente, versão de código, versão do catálogo, orçamento previsto/consumido, duração, status HTTP quando recebido, capacidades, estado, justificativa e limitações. O relatório S5 inclui, por par, preset/versão, referência e versão TPU, estado de aplicabilidade, janela de datas, número de chamadas, totais observados quando disponíveis e estados `query_status`/`sample_status`.

O resumo do lote lista todos os aliases solicitados, estado agregado S1/S2, contagem de pares S5 por estado e falhas/itens não executados. Resultado agregado não pode apagar falha ou inconclusão individual.

Relatórios não contêm chave, cabeçalhos de autenticação, payload bruto, CNJ completo, `_id`, `_source.id`, cursor, partes, query livre ou resposta não allowlisted. Queries reportadas são templates sanitizados. Evidências externas guardadas no repositório passam por revisão de sanitização; resposta bruta não é persistida.

Estados agregados:

- `VALIDATED`: todos os critérios exigidos para aquela capability ou par foram observados e têm evidência datada.
- `INCOMPATIBLE`: resposta observada contradiz o contrato ou critério testado; registrar a observação sanitizada.
- `INCONCLUSIVE`: evidência insuficiente, ausência de hits/amostra, erro, timeout, rate limit ou limite de orçamento.
- `NOT_RUN`: não executado, por exemplo por decisão da pessoa operadora ou bloqueio anterior.
- `NOT_APPLICABLE`: somente para par S5 com exclusão comprovada por TPU/competência; nenhuma chamada DataJud foi feita.

Não emitir um booleano geral de “Brasil validado” se qualquer endpoint relevante estiver pendente, inconclusivo ou incompatível. Expansões futuras poderão disponibilizar tribunal e preset progressivamente após aprovação explícita de suas evidências.

## Segurança, termos e dados

- Antes de cada probe real, revisar o texto vigente da Portaria CNJ nº 160/2020 e alterações, termos da API, limites de acesso e finalidade de uso local. Interromper se o uso pretendido não for coberto ou se houver autorização prévia exigida.
- Enviar somente consultas necessárias aos critérios desta SPEC e respeitar respostas 401/403/429. Não buscar processos sigilosos nem armazenar dados de partes no relatório.
- Credenciais vêm exclusivamente da configuração do backend e nunca são impressas. Relatórios sanitizados distinguem indisponibilidade, acesso negado, rate limit, resposta inválida e ausência de amostra.
- Atribuição da fonte e ressalvas de qualidade, atualidade e integridade devem acompanhar qualquer uso futuro dos dados conforme regra vigente do CNJ.

## Critérios de aceitação

- **AC1 — allowlist e lote:** somente aliases válidos são aceitos; lote tem de um a cinco tribunais; execução sem ambiente real explícito ou sem chave envia zero requisições.
- **AC2 — orçamento S1/S2:** teste confirma até seis requisições por tribunal, até 100 hits por requisição e ausência de retry; o resultado de um endpoint não altera o de outro.
- **AC3 — contrato S1/S2:** relatório classifica cada capability prevista, exige evidência para `VALIDATED` e não aprova paginação sem duas páginas não vazias, cursor avançado, ordem na fronteira e sobreposição examinada.
- **AC4 — aplicabilidade S5:** todos os quatro presets têm classificação por tribunal com referência/versionamento TPU; `NOT_APPLICABLE` e `INCONCLUSIVE` de aplicabilidade geram zero chamada DataJud.
- **AC5 — orçamento S5:** teste confirma no máximo duas requisições por par aplicável, janela máxima de 31 dias, no máximo 100 hits por chamada, espaçamento mínimo de um segundo e zero retry.
- **AC6 — classificação S5:** regra de query e amostra reproduz a SPEC-010; zero hits/sem resposta não é sucesso; menos de três documentos distintos não valida amostra.
- **AC7 — retentativa manual:** ausência de amostra não dispara nova janela nem busca histórica. Só novo intervalo informado manualmente inicia outra execução limitada.
- **AC8 — isolamento e sanitização:** testes provam ausência de acesso ao banco de produto e ausência de credencial, CNJ, IDs, cursores, partes e payload bruto nos relatórios.
- **AC9 — resultado parcial:** lote produz relatório por tribunal e resumo com estados distintos para sucesso, incompatibilidade, indisponibilidade e itens não executados, sem uma falha mascarar as demais.
- **AC10 — habilitação:** nenhum relatório ou conclusão de ferramenta altera automaticamente matriz de produção, fonte ativa, agenda ou disponibilidade de preset.
- **AC11 — consolidação e revisão de cobertura:** `agrojud-datajud-multitribunal-matrix merge` combina os resumos e relatórios por lote em uma matriz 33 × 4, com estado S1/S2 por capability, estado S5 por par, tentativas e referências/hashes de origem. Evidência remota `VALIDATED` ou `INCOMPATIBLE` exige relatório de ambiente `real`, confirmação dos termos e contagem de requisições correspondente; relatório bloqueado no preflight não pode virar evidência. O mesmo `run_id` idêntico é idempotente; conteúdo divergente para o mesmo `run_id`, schema incompatível, alias/arquivo fora do contrato ou versão de allowlist diferente falha sem combinar os dados.
- **AC12 — histórico, versão e revisão auditável:** tentativas repetidas são preservadas; `VALIDATED` resiste a tentativa posterior inconclusiva, enquanto `INCOMPATIBLE` continua bloqueando até resolução explícita que selecione uma tentativa `VALIDATED` do mesmo alvo e versão atual. S5 de catálogo, preset ou TPU desatualizados fica visível como histórico obsoleto e não valida o alvo atual. `review` grava cópia separada com pessoa revisora, decisão, justificativa, data UTC e SHA-256 dos bytes da matriz de entrada. Cobertura nacional só é afirmada quando todas as capabilities S1/S2 e pares S5 aplicáveis estão válidos e a revisão aprova a matriz.

## Testes necessários

Usar HTTPX MockTransport. Cobrir lote de cinco aliases e rejeição de seis; alias/URL inválidos; chave ausente; ambiente demo; 401/429 interrompendo chamadas futuras do lote e marcando alvos restantes `NOT_RUN`; 403, 5xx, timeout e envelope inválido isolados; tribunal retornado diferente do alvo; campos essenciais ausentes; CNJ fabricado/revelado no relatório; sort recusado; página vazia; duas páginas com cursor avançando, repetido e sobreposição; limites de seis requisições e `size <= 100`.

Para S5, cobrir cada estado de aplicabilidade, garantir zero chamada para não aplicável/inconclusivo, códigos ausentes/inesperados, zero hit, 1–2 amostras, três IDs distintos, IDs repetidos, janela de 31 dias e rejeição de 32 dias, exatamente duas chamadas, espaçamento, sem retry, e segunda tentativa apenas com nova janela manual. Inspecionar relatórios e confirmar nenhuma conexão ao banco.

Para a consolidação, cobrir os sete lotes que compõem os 33 aliases, deduplicação idempotente de `run_id`, rejeição de divergência e allowlist incompatível, preservação das tentativas, conflito incompatível/validado, S5 obsoleto por versão, resolução com seleção de tentativa validada, cópia de revisão vinculada ao SHA-256 e exit code 1 para incompatibilidade S5. Merge e revisão devem operar offline.

Probes remotas são limitadas, explícitas, separadas dos testes locais e executadas somente após conferência dos termos vigentes. Testes sintéticos provam o comportamento da ferramenta, não a aceitação externa de tribunal.

## Erros e casos-limite

- Endpoint indisponível: `INCONCLUSIVE`, sem repetir; demais tribunais continuam dentro do próprio orçamento. HTTP 429 interrompe as chamadas restantes do lote, que ficam `NOT_RUN`.
- Endpoint válido com filtro que retorna zero: filtro e tema ficam `INCONCLUSIVE` até nova janela escolhida manualmente; não declarar tribunal vazio.
- Resultado de outro tribunal, data inválida, mudança de envelope ou divergência de identidade: registrar incompatibilidade específica e não normalizar com fallback.
- Resposta parcial, cursor repetido, duplicação ou reordenação entre páginas: paginação não validada; preservar os demais resultados observados.
- TPU ou competência sem evidência bastante: aplicabilidade `INCONCLUSIVE`, não consultar o DataJud até classificação revisada.
- Código oficial alterado após a execução: evidência do preset antigo não promove a nova versão; repetir S5 para a versão modificada.
- Amostra sem empates no sort primário: registrar esse limite; não alegar que o desempate foi exercitado.

## Evidências e conclusão

### Implementação e evidências locais — 11/10/2026

- Implementado `backend/src/agrojud/sources/datajud_multitribunal_validation.py` como ferramenta isolada, com allowlist versionada `2026-10-11` para os 33 endpoints estaduais/federais, lotes explícitos de até cinco, budgets S1/S2 e S5, matriz de aplicabilidade TPU, relatórios sanitizados por tribunal e lote e gate `PENDING_HUMAN_REVIEW`. A versão de catálogo registrada vem do catálogo ativo local; a matriz TPU exige versão/data e referência oficial fornecidas pela pessoa operadora antes de classificar aplicabilidade como `APPLICABLE` ou `NOT_APPLICABLE`.
- `agrojud-datajud-multitribunal-probe` aceita somente aliases allowlisted, datas e destino de relatório explícitos. Exige `AGROJUD_ENV=real`, chave no ambiente do backend e confirmação da revisão dos termos antes de enviar HTTP. Sem esses gates, registra execução sem chamadas. Não importa nem acessa banco de produto e não altera o adaptador, fonte ativa, agenda ou presets em produção.
- `agrojud-datajud-multitribunal-matrix merge --batch <batch-1.json> ... --output <matriz.json>` é offline. Valida schema, allowlist, endpoints, os dez estados S1/S2, os quatro pares S5, referências confinadas ao diretório do lote e coerência entre ambiente, confirmação dos termos e contagens. Evidência remota `VALIDATED` ou `INCOMPATIBLE` sem chamada real não é aceita. A matriz contém os 33 alvos, tentativas históricas, versão atual dos presets/catálogo/TPU e hashes dos relatórios-fonte. `VALIDATED` sobrevive a `INCONCLUSIVE`; incompatibilidades e divergências permanecem explícitas. Uma mudança de catálogo, preset ou TPU mantém a evidência S5 antiga como obsoleta e exige nova execução.
- `agrojud-datajud-multitribunal-matrix review --matrix <matriz.json> --output <revisada.json> --reviewer <identificação> --decision APPROVED --rationale <justificativa>` gera um arquivo novo, sem alterar a matriz recebida; `--decision` também aceita `REJECTED`. Uma resolução opcional em JSON (`schema_version: 1`, `resolutions: [...]`) identifica `alias`, `target` (`s1_s2` ou `s5`), `capability` ou `preset_id`, `selected_run_id` e `rationale`; só aceita tentativa validada e atual para o mesmo alvo. A revisão registra SHA-256 dos bytes da entrada, pessoa revisora e data UTC. Aprovar uma matriz parcial não declara cobertura nacional.
- O comando de probe retorna exit code `1` quando S1/S2 ou qualquer par S5 contém incompatibilidade; preflight bloqueado continua retornando `2`. Nenhum comando de merge/revisão consulta a API, banco de produto ou altera configuração de produção.
- Testes com HTTPX MockTransport cobrem budgets, paginação, classificação S1/S2 e S5, sanitização, gates, isolamento, resultados parciais, matriz 33 × 4, merge de sete lotes, deduplicação, versões obsoletas, revisão e resolução. Nenhuma probe remota foi executada nesta entrega; não há evidência de compatibilidade para novo tribunal e o runtime permanece restrito ao TJGO.

Exemplo de uso a partir de `backend/`, após guardar os sete resumos batch e seus relatórios no diretório `reports/`:

```bash
uv run --no-sync agrojud-datajud-multitribunal-matrix merge \
  --batch 'reports/batch-<run-1>.json' \
  --batch 'reports/batch-<run-2>.json' \
  --batch 'reports/batch-<run-3>.json' \
  --batch 'reports/batch-<run-4>.json' \
  --batch 'reports/batch-<run-5>.json' \
  --batch 'reports/batch-<run-6>.json' \
  --batch 'reports/batch-<run-7>.json' \
  --output reports/matriz-multitribunal.json

uv run --no-sync agrojud-datajud-multitribunal-matrix review \
  --matrix reports/matriz-multitribunal.json \
  --output reports/matriz-multitribunal-revisada.json \
  --reviewer "Nome da pessoa revisora" \
  --decision APPROVED \
  --rationale "Revisão local registrada."
```

Os nomes `batch-<run-n>.json` são placeholders; use os nomes reais, que incluem o UUID gravado pelo probe.

Para resolver um conflito, acrescente `--resolutions resolucoes.json`. O arquivo usa `schema_version: 1` e uma lista `resolutions` com `alias`, `target`, `capability` (S1/S2) ou `preset_id` (S5), `selected_run_id` e `rationale`.

Validação executada em PostgreSQL de teste isolado, sem conexão ao banco operacional:

```text
COMPOSE_PROJECT_NAME=agrojud-spec024-20261011 TEST_DB_HOST_PORT=57624 DATAJUD_API_KEY= docker compose --project-name agrojud-spec024-20261011 --env-file .env.test -f compose.test.yaml --profile test run --rm --build --no-deps tests sh -lc 'uv run --no-sync ruff check src tests && uv run --no-sync ruff format --check src tests && uv run --no-sync mypy src && uv run --no-sync pytest -q tests/test_datajud_multitribunal_validation.py'
Ruff: aprovado; formatação: 104 arquivos aprovados; mypy: 74 módulos sem erros; testes SPEC-024: 50 passaram.

COMPOSE_PROJECT_NAME=agrojud-spec024-20261011 TEST_DB_HOST_PORT=57624 DATAJUD_API_KEY= docker compose --project-name agrojud-spec024-20261011 --env-file .env.test -f compose.test.yaml --profile test run --rm --no-deps tests sh -lc 'uv run --no-sync pytest'
Suíte backend completa: 350 passaram; um aviso de depreciação de `starlette.testclient` com `httpx`.

docker build -f backend/Dockerfile --target production -t agrojud-spec024-prod:local backend
docker run --rm --entrypoint uv agrojud-spec024-prod:local run --no-sync agrojud-datajud-multitribunal-probe --help
docker run --rm --entrypoint uv agrojud-spec024-prod:local run --no-sync agrojud-datajud-multitribunal-matrix --help
Build de produção e publicação/ajuda dos dois CLIs: aprovados.
```

Os critérios AC1–AC10 passaram pelos testes locais e verificações acima. AC11–AC12 foram verificados pelo merge de sete lotes, tentativas versionadas, revisão com SHA-256 e resolução explícita de conflito. A matriz de saída continua `PENDING_HUMAN_REVIEW`; nenhum teste simulou compatibilidade DataJud real nem habilitou cobertura nacional ou endpoint no runtime. A revisão operacional da matriz de evidências externas depende de execuções autorizadas por lote.
