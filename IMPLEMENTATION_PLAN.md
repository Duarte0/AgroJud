# IMPLEMENTATION_PLAN — AgroJud Radar

Data: 09/10/2026.

Status: M0 concluído em 08/10/2026. SPEC-002 e a entrega local da SPEC-003 foram concluídas em 08/10/2026. SPEC-004/S3 e SPEC-005/006 foram concluídas localmente em 09/10/2026; M2 está completo. SPEC-007/M3, SPEC-008 e SPEC-009 foram concluídas localmente em 09/10/2026; M4 e S4 passaram nos critérios locais com PostgreSQL isolado, HTTP simulado e subprocessos interrompidos antes/depois do commit. SPEC-010 concluiu localmente catálogo e investigação TPU em 09/10/2026. SPEC-011/M5 concluiu API operacional e contrato OpenAPI em 09/10/2026, com PostgreSQL isolado, tipos gerados e testes HTTP. SPEC-012/M6 concluiu localmente o frontend de coleta e consulta em 09/10/2026, validado com Playwright sobre API, worker e banco demo isolados. SPEC-013 concluiu localmente triagem humana e histórico em 09/10/2026, validada com PostgreSQL isolado, 222 testes backend, 42 testes frontend e 9 cenários Playwright. SPEC-014/M7 concluiu localmente regras estruturadas, sinais e reprocessamento em 09/10/2026, com 227 testes backend, 44 testes frontend, build e 10 cenários Playwright. SPEC-015 concluiu localmente a primeira entrega de M8 em 09/10/2026, com acompanhamento auditável, atualização manual por CNJ, 237 testes backend, 47 frontend, build e 11 cenários Playwright. SPEC-016 concluiu localmente baselines por representação, novidades idempotentes/revisáveis e evidências API/UI em 09/10/2026, com 245 testes backend, 49 testes frontend, build e 12 cenários Playwright aprovados. SPEC-017 concluiu localmente buscas salvas versionadas, agendas recuperáveis e alternância persistente da fila em 09/10/2026, com 261 testes backend, 49 testes frontend, build e 13 cenários Playwright aprovados. M8 está completo localmente. Regras reais e integração DataJud/TJGO permanecem desabilitadas enquanto S5/S1/S2 não tiverem validação externa.

## 1. Estado atual e orientação

Estado inspecionado durante o planejamento, antes da criação deste documento:

- `PRD.md` lido integralmente.
- Repositório continha somente o PRD, versionado no commit `21e2fec`.
- Árvore de trabalho limpa antes desta entrega documental.
- Naquele momento não havia aplicação, testes, migrations, Docker ou `AGENTS.md` aplicável.

Estado após a implementação de SPEC-001, confirmado em 08/10/2026:

- Backend FastAPI e worker compartilham o pacote e a imagem; PostgreSQL 18.6 é persistente e não publica porta nos ambientes demo/real.
- Alembic registra a revisão inicial sem tabelas de produto. A migration é explícita; readiness compara revisões sem alterar o banco.
- `.env.demo.example`, `.env.real.example` e `.env.test.example` orientam projetos Compose e bancos isolados; testes recusam banco sem sufixo `_test` ou URL idêntica à operacional.
- O frontend contém somente documentação para SPEC-012. DataJud, domínios processuais, fila e coleta continuam fora de M0.

Estado após a implementação de SPEC-002, confirmado em 08/10/2026:

- Contratos Python, compilação allowlisted da consulta TJGO, cliente HTTPX de tentativa única e fonte sintética determinística estão entregues em `backend/src/agrojud/sources/`.
- Testes HTTP usam MockTransport; fixtures e payload bruto são locais e sintéticos. API e worker ainda não iniciam consultas nem persistem resultados.
- O ambiente demo seleciona somente fonte sintética; o ambiente real seleciona somente DataJud e exige `DATAJUD_API_KEY`; teste exige transporte simulado. Não há fallback entre fontes.
- Nenhuma chamada ao DataJud foi executada. Shape do TJGO, identidade de origem e paginação permanecem sem validação externa em SPEC-003; SPEC-010 concluiu o catálogo local, mas filtros e exemplos reais seguem sem validação.

Estado após a implementação de SPEC-003, confirmado em 08/10/2026:

- `agrojud-datajud-probe` limita cada execução a seis requisições, cem hits por página e nenhuma repetição automática; escreve relatório sanitizado e não acessa o banco de produto.
- A probe limitada ao endpoint TJGO expirou após 20.187 ms, sem status HTTP. Depois, uma resposta manual `match_all`, `size: 1` confirmou envelope, campos essenciais e `_id == _source.id` em um hit; `dataAjuizamento` veio como `YYYYMMDDHHMMSS`. A evidência sanitizada está em `docs/evidence/datajud-tjgo-amostra-manual-2026-10-09.json`.
- A resposta manual não exercitou filtro de data, busca exata por CNJ, sort nem paginação. SPEC-003 está DONE para ferramenta, testes e relatório; essas capacidades de S1/S2 seguem pendentes e não liberam a fonte real. SPEC-010 entregou catálogo e presets locais, ainda desabilitados no modo real enquanto a evidência de filtros e amostras seguir INCONCLUSIVE.

Estado após a implementação de SPEC-004/S3, confirmado em 09/10/2026:

- `agrojud.domain.canonical_json` define hash SHA-256 de JSON compacto UTF-8 com chaves ordenadas, preservação de tipos/null/ordem dos arrays e rejeição de valores não JSON.
- `agrojud.domain.occurrence_identity` define identidade por representação, hash do movimento normalizado e ordinal; complementos são ordenados para comparação sem perder multiplicidade. O histórico é cumulativo e as correspondências auxiliares alteradas preservam referências e ambiguidade.
- Horário com fuso é normalizado para UTC e mantém o original; horário sem fuso é marcado ambíguo e comparado pelo valor original. A versão do normalizador é metadado explícito.
- Fixtures sintéticas e decisão estão em `backend/tests/fixtures/occurrence_reconciliation.json` e `docs/decisions/S3-identidade-e-reconciliacao.md`. Não houve schema, migration, persistência nem chamada ao DataJud.
- Ruff, formatação, mypy e 92 testes passaram em container Python 3.14.8. A imagem de produção API/worker foi construída e importou os novos módulos; evidência integral em [SPEC-004](specs/SPEC-004-identidade-e-reconciliacao.md). Isso comprova o algoritmo local, não a forma ou estabilidade de movimentos reais.

Estado após a implementação de SPEC-005, confirmado em 09/10/2026:

- A migration `20261009_0002` adiciona processos, representações, versões de payload, coletas, observações, resultados e assuntos pesquisáveis. Aplicou em PostgreSQL vazio e após a revisão SPEC-001.
- `agrojud.services.ingestion.ingest_page` persiste páginas sem HTTP ou commit próprio, com savepoint, identidade de origem estável, hash canônico, replay idempotente e detecção de conflito/colisão. A representação aponta para sua versão local mais recente e registra regressões de data de ajuizamento separadamente.
- Ruff, formatação, mypy e os 104 testes passaram em PostgreSQL de teste isolado. Configurações Compose demo/real/test e build de produção API/worker passaram; evidência e contagens de replay/rollback em [SPEC-005](specs/SPEC-005-persistencia-de-capas-e-payloads.md).
- A validação usa somente fixtures sintéticas; nenhuma chamada ao DataJud ou acesso ao banco operacional ocorreu. M2 continua parcialmente aberto para movimentos e quarentena, que pertencem à SPEC-006. Jobs, checkpoints, API e frontend não foram antecipados.

Estado após a implementação de SPEC-006, confirmado em 09/10/2026:

- A migration `20261009_0003` adiciona ocorrências versionadas por representação e multiplicidade, snapshots de normalização associados a versões de payload, presença/comparação por snapshot, rejeições localizadas e histórico de resolução.
- `ingest_page` grava capas, ocorrências e rejeições na mesma transação de página. Listas ausentes, `null`, inválidas ou parcialmente rejeitadas ficam incompletas; lista vazia explícita é válida. Reordenação não muda identidades e ausência em snapshot incompleto não é registrada como desaparecimento.
- `agrojud-quarantine-reprocess` exige IDs explícitos, abre uma transação por hit e usa apenas conteúdo local. Resolução é auditada sem reescrever a observação nem o resultado original da coleta; ocorrências derivadas preservam a primeira observação original e snapshots guardam o horário do reprocessamento.
- Ruff, formatação, mypy e 126 testes passaram em PostgreSQL 18.6 isolado. As migrations foram aplicadas em banco vazio e sobre revisões anteriores; Compose demo/real/test e build da imagem API/worker passaram. Evidência completa em [SPEC-006](specs/SPEC-006-movimentos-e-quarentena.md).
- A validação usa somente fixtures sintéticas; nenhuma chamada ao DataJud ou acesso ao banco operacional ocorreu. Jobs, API, frontend e baseline não foram antecipados.

Estado após a implementação de SPEC-007, confirmado em 09/10/2026:

- A migration `20261009_0004` acrescenta jobs vinculados a collections, tentativas, eventos append-only e checkpoint inicial. Índices parciais garantem uma operação ativa equivalente e permitem reserva eficiente; constraints e triggers protegem estados, posse e snapshot imutável.
- `JobService` implementa enqueue concorrente idempotente, claim com `FOR UPDATE SKIP LOCKED`, token UUID, lease verificada com `clock_timestamp()`, heartbeat, cancelamento, finish, inspect e checkpoint CAS. Gravações protegidas bloqueiam primeiro o job e revalidam lease/cancelamento antes do commit.
- `LeasedWorker` mantém o heartbeat em sessão/thread separada, não mantém transação durante handler bloqueado e suspende gravação após falha do heartbeat. Erros inesperados são registrados com código/resumo sanitizados. Nenhum handler de coleta de produção foi registrado.
- PostgreSQL 18.6 isolado validou migration em banco vazio e sobre SPEC-006 com collection preexistente; 142 testes passaram incluindo disputa em duas sessões, lease vencida durante gravação, cancelamento concorrente, heartbeat independente, CAS e recuperação após nova instância. Ruff check/format, mypy, Compose demo/real/test, build de produção API/worker e `agrojud-worker --check` passaram. Evidência detalhada em [SPEC-007](specs/SPEC-007-jobs-leases-e-posse.md).
- Naquele ponto, após SPEC-007, a evidência era local e sintética: SPEC-008/009 ainda precisavam implementar paginação, retries e coleta recuperável; nenhuma chamada ao DataJud havia ocorrido.

Estado após a implementação de SPEC-008, confirmado em 09/10/2026:

- `JobService` confirma atomicamente ingestão da página, quarentena, contadores, evento e checkpoint CAS sob posse válida. A chamada HTTP fica fora da transação; replay usa chave estável pela coleta e revisão anterior. Não foi necessária migration.
- Os handlers `discovery` e `refresh_number` implementam paginação, congelamento de consulta/sort, cursor integral, orçamento inicial de 2.000 hits, página padrão 100 e término por página vazia. Página curta não prova exaustão; falha posterior preserva cobertura parcial e sinaliza dados persistidos. Busca por número percorre todas as representações.
- O contrato interno de continuação permite até 2.000 hits adicionais preservando cursor, revisão e contadores. Comandos e ciclos de retomada, retries persistentes e invalidação de cursor permanecem na SPEC-009.
- Ruff check/format, mypy, 153 testes PostgreSQL, Compose demo/real/test, build de produção API/worker, `agrojud-worker --check` e import runtime do handler passaram. Evidência e rastreabilidade de critérios em [SPEC-008](specs/SPEC-008-paginacao-e-checkpoints.md).
- Naquele ponto, a validação usava PostgreSQL isolado e HTTP simulado. A implementação de retries, recuperação e S4 está registrada a seguir; nenhuma chamada ao DataJud foi feita.

Estado após a implementação de SPEC-009, confirmado em 09/10/2026:

- A migration `20261009_0005` persiste ciclo/tentativas por página, recuperações, invalidação de cursor, predecessor e limitador/cooldown global por fonte. O agendamento usa `next_attempt_at`; migrations aplicam em banco vazio e sobre SPEC-007/008 sem apagar jobs existentes.
- HTTP consome até cinco tentativas por página, registra cada uma antes da chamada, faz retry apenas para falhas transitórias permitidas, aplica full jitter/`Retry-After` e agenda sem manter lease. Persistência transitória é limitada a cinco tentativas por página e reconcilia revisão para commit de resultado desconhecido. Integridade não é retry transitório.
- `resume`, `continue` e `restart_scan` preservam suas semânticas distintas e eventos. Cursor rejeitado requer reinício explícito. Cooldown fica compartilhado entre jobs da mesma fonte. Recuperações por lease expirada falham com `recovery_exhausted` após cinco recuperações sem progresso; indisponibilidade de banco deixa a lease expirar sem declarar falha não confirmada.
- S4 foi comprovado por testes concorrentes com duas sessões/barreira e subprocessos encerrados pelo sistema antes/depois da confirmação da página. Checksums, contagens, cursor e revisão após recuperação foram comparados à execução contínua. A suíte completa passou: 173 testes; Ruff check/format e mypy passaram.
- `docker compose config --quiet` passou nos ambientes demo/real/test, a imagem de produção API/worker foi compilada e `agrojud-worker --check` passou. Evidência detalhada em [SPEC-009](specs/SPEC-009-retries-e-recuperacao.md).
- Toda a validação local usa PostgreSQL isolado e fonte/HTTP sintéticos. Nenhuma requisição DataJud foi feita. S2 continua inconclusivo, portanto modo/paginação reais permanecem bloqueados.

A implementação continuará em entregas pequenas, verificáveis e cumulativas. M0 estabelece a fundação local; SPEC-005/006 completam M2; SPEC-007 entrega a infraestrutura de M3; as próximas etapas introduzirão processamento conforme suas SPECs.

Cada etapa deverá registrar comandos executados, resultados, limitações e evidências. Implementação local, integração real e prontidão para uso profissional são conclusões distintas. Nenhuma etapa de aplicação deve ser considerada concluída pela existência deste documento.

## 2. Decisões técnicas e ajustes ao PRD

| Decisão | Aplicação e justificativa |
| --- | --- |
| Antecipar Docker e PostgreSQL | Usá-los desde a fundação para testar transações e persistência no ambiente real. |
| Backend compartilhado | API e worker como processos separados do mesmo pacote Python, compartilhando domínio e persistência. |
| Persistência síncrona | SQLAlchemy com Psycopg, endpoints FastAPI síncronos e HTTPX síncrono no worker. Evitar complexidade assíncrona desnecessária para a concorrência inicial. |
| Transações curtas | Requisições DataJud fora de transações; posse do job verificada novamente antes de gravar. |
| Migrations incrementais | Criar tabelas por capacidade entregue, sem implementar antecipadamente todo o modelo do PRD. |
| Dois ambientes isolados | Bases e volumes distintos para demonstração sintética e dados reais; sem troca automática de fonte após erro. |
| Estados e cobertura separados | `status` descreve execução; cobertura e motivo descrevem limites, rejeições ou dados parcialmente persistidos. Uma falha com dados não vira sucesso. |
| Identidade técnica conservadora | Preservar versões e multiplicidade; não prometer identificar juridicamente cada movimento. |
| Interface por polling | Consultar jobs periodicamente enquanto ativos; adiar WebSocket/SSE. |
| Sem cron externo | Agendamento persistido, verificado pelo worker, com controle transacional de disparos. |
| Validação real independente | Falha do TJGO não bloqueia testes sintéticos, mas impede concluir a integração real. |

A fila utilizará reserva transacional com `FOR UPDATE SKIP LOCKED`. Esse mecanismo serve à seleção de trabalho; não substitui lease, token de posse ou verificação de validade na gravação. Referência: [SELECT e bloqueios no PostgreSQL](https://www.postgresql.org/docs/current/sql-select.html).

### Contratos a manter desde o início

- Processo local agrupado pelo número CNJ; representações distintas por origem.
- Payload bruto versionado por hash, separado das observações feitas em cada coleta.
- Dados humanos separados dos dados importados.
- Persistência da página, contadores confirmados e checkpoint na mesma transação.
- Nenhuma exclusão de processo ou movimento histórico por ausência em consulta posterior.
- Nenhuma resposta indisponível convertida em lista vazia.
- `concluído` significa consulta encerrada sem rejeições conhecidas, não completude universal da fonte.

## 3. Critical Path

**Caminho do núcleo demonstrável:**

`M0 Fundação → M1 Contrato → M2 Persistência → M3 Jobs → M4 Coleta recuperável → M5 API → M6 Frontend`

**Caminho do MVP completo:**

`M6 → M7 Triagem e regras → M8 Acompanhamento e agendamento → M9 Exportação e entrega`

**Dependência externa obrigatória para declarar integração real:**

`S1 Contrato TJGO + S2 Paginação real → validação real de M4 → aceite real em M9`

A fonte indisponível permite avançar com o adaptador sintético e contratos provisórios documentados. Os testes locais não encerram S1/S2, e o modo real permanece sem aceite.

A visão geral da base local foi entregue depois do gate de recuperação do núcleo na SPEC-018. A exportação e a operação integrada seguem nas SPEC-019 e SPEC-020.

## 4. Validation Spikes

Spikes devem produzir evidência e uma decisão, sem evoluir para funcionalidades paralelas.

| Spike | Experimento e resultado esperado | Dependência |
| --- | --- | --- |
| **S1 — Contrato TJGO** | Consulta limitada: autenticação, formato de hits, número CNJ, identidade de origem, campos ausentes, datas, assuntos e movimentos. Gerar matriz campo/documentação/observação. | Antes de ativar o adaptador real. |
| **S2 — Paginação** | Obter duas páginas; verificar cursor retornado, empates e repetição. Testar `@timestamp` com desempate pelo identificador apenas se aceito pelo endpoint. Documentar limites da fonte mutável. | Antes do aceite de paginação real. |
| **S3 — Reconciliação** | Fixtures com reordenação, duplicatas indistinguíveis, mudança textual, retirada e reaparecimento de movimentos, além de capas distintas. Fixar a política conservadora descrita em M2. | Antes de novidades e sinais. |
| **S4 — Atomicidade e posse** | Dois consumidores em teste, interrupção antes/depois do commit, lease expirada e gravação tardia. | Antes de concluir M4. |
| **S5 — Catálogo temático** | Validar códigos e hierarquia TPU dos presets e sinais; verificar exemplos reais sem confundir validade do código com presença no TJGO. | Antes de ativar cada preset/regra real. |

Regras para os spikes:

- Consultas remotas pequenas, com timeout e orçamento de tentativas explícitos.
- Evidências sem credenciais; não versionar lotes reais completos por conveniência.
- Fixtures públicas do projeto serão sintéticas.
- Não assumir suporte a PIT, estabilidade do identificador ou identidade global dos movimentos.
- Se não houver ordenação segura validada, não ativar paginação real como concluída; preservar o diagnóstico.
- Não repetir indefinidamente probes quando a fonte estiver indisponível.

## 5. Milestones

### M0 — Fundação executável

**Dependência:** nenhuma.

**Status:** concluído em 08/10/2026; ver [SPEC-001](specs/SPEC-001-fundacao-local.md).

**Implementar:**

- Estrutura `backend/`, `frontend/` e documentação técnica.
- Pacote Python, configuração tipada, dependências travadas e comandos de desenvolvimento.
- Compose inicial com PostgreSQL persistente, API mínima e comando separado para worker.
- Alembic, pytest, lint e integração contínua inicial.
- Bancos separados para testes, demonstração e execução real.
- Liveness da API e readiness de banco/migration; indisponibilidade DataJud não derruba readiness local.
- Configuração de credencial exclusivamente no backend.

**Concluir quando:**

- [x] Ambiente sobe seguindo o README e os Compose demo/real/test são válidos.
- [x] API acessa PostgreSQL; liveness independe do banco e readiness exige revisão no head.
- [x] Migration inicial executa em banco vazio e cria somente `alembic_version`.
- [x] Reinício do PostgreSQL de teste preserva o registro de diagnóstico; a tabela é removida após a prova.
- [x] Testes criam bancos temporários com sufixo `_test` e não acessam banco operacional.

**Demonstração:** subir serviços, verificar saúde, reiniciar e comprovar persistência.

**Evidências:** `./scripts/smoke-compose.sh demo`; `./scripts/verify-persistence.sh`; Ruff check e format, mypy e 10 testes passaram em container Python 3.14.8 com PostgreSQL 18.6. O workflow `.github/workflows/ci.yml` executa PostgreSQL real sem acesso ao CNJ. A porta 8000 já estava ocupada no host, então o smoke local usou a porta documentada por override (`API_PORT=18002`).

### M1 — Adaptador DataJud e contrato verificável

**Dependência:** M0.

**Status em 09/10/2026:** parcial. SPEC-002 está DONE para contratos/adaptadores, SPEC-003 está DONE para probe/testes/relatório e SPEC-010 está DONE para catálogo/investigação local. O milestone não está concluído: os aceites externos S1/S2 da SPEC-003 permanecem INCONCLUSIVE.

**Implementar:**

- Interface de fonte com operações de consulta paginada e pesquisa por número.
- Adaptadores HTTP DataJud e sintético determinístico.
- DTOs tolerantes a campos opcionais, com validação dos campos essenciais.
- Categorias de erro: consulta inválida, autenticação, limite remoto, rede, indisponibilidade e contrato inesperado.
- Catálogo versionado de presets; entregue localmente na SPEC-010. Itens sem evidência exigida permanecem desativados no modo real.
- Ferramenta limitada de diagnóstico para S1/S2 (`agrojud-datajud-probe`, entregue na SPEC-003).

**Entregue pela SPEC-002:** consulta imutável TJGO com filtros allowlisted e intervalo semiaberto; busca exata por CNJ; DTOs que preservam hit bruto, metadados, sort e cursor; erros tipados; HTTPX síncrono com timeouts 5/20/20/5 segundos, redirects desativados e sem retry; fixtures sintéticas explícitas e mutáveis entre execuções; isolamento de fonte por ambiente.

**Pendências mantidas nas unidades próprias:** SPEC-010 registrou códigos TPU e estados de habilitação, sem ativar presets reais. Filtro por data, busca exata, sort e paginação reais continuam pendentes na SPEC-003. O probe limitado expirou; uma amostra manual posterior confirmou somente envelope, campos essenciais e correspondência de IDs em um hit. Nenhuma capacidade não observada foi habilitada.

**Evidência da SPEC-003:** 67 testes passaram, com Ruff, formatação e mypy aprovados. Os testes HTTP usam MockTransport, incluindo o formato compacto observado para `dataAjuizamento`. A probe limitada expirou em 20.187 ms sem status HTTP; a amostra manual validou somente envelope, campos essenciais e relação de IDs para um hit. As evidências estão em `docs/evidence/datajud-tjgo-validacao-2026-10-08.json` e `docs/evidence/datajud-tjgo-amostra-manual-2026-10-09.json`. S1/S2 seguem pendentes e não liberam coleta real.

**Evidência da SPEC-010:** o SGT público retornou a versão TPU `06/10/2026`; pesquisa e aplicabilidade por código estão em `docs/evidence/catalogo-tematico-tpu-2026-10-09.json`. Os filtros DataJud e exemplos estruturados do TJGO permanecem INCONCLUSIVE, então os presets reais continuam desabilitados e S5 permanece aberto. Ruff, formatação e mypy passaram; pytest passou com 191 testes PostgreSQL isolados. A imagem de produção carregou o JSON e o wheel incluiu `thematic_catalog.v1.json`. Sem migration e sem chamada ao DataJud.

**Concluir quando:**

- Testes distinguem resultado vazio de erro.
- Resposta malformada não é aceita como página vazia.
- Cursor e metadados são preservados sem reconstrução arbitrária.
- Evidência real e cobertura sintética estão registradas separadamente.

**Demonstração:** executar a mesma consulta pelo adaptador sintético e apresentar o relatório do probe real, inclusive se indisponível.

**Evidência de SPEC-002:** lint, formatação, mypy e 57 testes passaram em container Python 3.14.8 com PostgreSQL 18.6 isolado; Compose validou demo/real/test; imagem de produção API/worker foi construída e os adaptadores foram importados nela. Os testes HTTP usaram MockTransport; não houve chamada externa. Resultado registrado em [SPEC-002](specs/SPEC-002-contratos-e-adaptadores-de-fonte.md).

### M2 — Persistência idempotente de uma página

**Dependência:** M1; S3 para política de movimentos.

**Entregas:**

1. [x] Processo, representação, versão de payload, coleta, observação e resultado — entregue pela SPEC-005.
2. [x] Movimentos normalizados, associação à versão e quarentena — entregue pela SPEC-006.

**Política de identidade:**

- Número CNJ armazenado como texto.
- Representação vinculada à identificação de origem validada; mudança de origem preserva uma representação distinta.
- Hash canônico do conteúdo para reaproveitar versões idênticas.
- Ocorrências de movimentos por representação, conteúdo normalizado e índice de multiplicidade.
- Ordem dos movimentos no array não define identidade.
- Alteração textual mantém evidências anteriores e é classificada como alteração observada, sem declarar novo ato jurídico.
- Correspondência ambígua não será resolvida por aproximação silenciosa.
- A política técnica local de S3 está documentada e testada em [`SPEC-004`](specs/SPEC-004-identidade-e-reconciliacao.md); persistência e evidência de fonte real continuam em unidades próprias.

**Concluir quando:**

- Ingerir a mesma página duas vezes não multiplica efeitos.
- Duas capas do mesmo CNJ permanecem distintas.
- Movimentos idênticos repetidos no mesmo payload preservam multiplicidade.
- Reordenação não cria novidades.
- Registro inválido fica em quarentena identificável.
- Migration funciona em banco vazio e no banco da etapa anterior.

**Demonstração:** ingerir, repetir e comparar contagens e versões.

SPEC-005 e SPEC-006 completam M2. Os critérios de persistência local foram validados com fixtures sintéticas; isso não valida movimentos reais do DataJud.

### M3 — Fila persistente e posse do job

**Dependência:** M2.

**Status:** concluído localmente em 09/10/2026; ver [SPEC-007](specs/SPEC-007-jobs-leases-e-posse.md).

**Implementar:**

- Job, tentativas, eventos de execução e checkpoint.
- Reserva com `SKIP LOCKED`, token de posse e lease.
- Heartbeat usando sessão de banco independente da ingestão.
- Recuperação de execução abandonada.
- Cancelamento cooperativo.
- Unicidade de consulta equivalente ativa, sustentada por restrição no banco.
- Logs estruturados por job e tentativa.

**Defaults iniciais configuráveis:**

- Lease de 120 segundos.
- Heartbeat a cada 20 segundos.
- Um worker operacional; dois consumidores apenas nos testes de disputa.
- Relógio do banco para validade de posse.

**Concluir quando:**

- Dois consumidores não reservam o mesmo job simultaneamente.
- Worker antigo não confirma página após perder a posse.
- Job abandonado volta a ser executável.
- Cancelamento mantém páginas confirmadas e impede avanço posterior.

**Demonstração:** encerrar um consumidor e recuperar o trabalho com outro.

**Evidência:** migration `20261009_0004` aplicada em banco vazio e após SPEC-006 com collection preexistente. Ruff check, Ruff format check, mypy e 142 testes PostgreSQL passaram. Testes concorrentes comprovaram enqueue/claim únicos, token antigo rejeitado, rollback quando a lease vence durante gravação, heartbeat em sessão independente, ordem entre cancelamento e commit e persistência de tentativas/eventos/checkpoint após nova instância. Compose demo/real/test, imagem de produção API/worker e `agrojud-worker --check` passaram; em projeto e volume isolados, API/DB aplicaram migrations e `/health/live` e `/health/ready` responderam com sucesso. Nenhuma fonte real foi consultada; nenhum handler de coleta de produção foi antecipado.

### M4 — Coleta paginada recuperável

**Dependência:** M3; S2 para modo real.

**Implementar em três entregas:**

1. [x] Paginação, limites e contrato interno de continuação com commit atômico de página/checkpoint (SPEC-008 concluída localmente).
2. [x] Retries persistentes, limite por página, backoff/cooldown e recuperação por lease (SPEC-009 concluída localmente).
3. [x] Comandos/ciclos de retomada, continuação e reinício explícito após invalidação segura de cursor (SPEC-009 concluída localmente).

**Definições:**

- Página de 100 e orçamento inicial de 2.000 registros de origem.
- Consulta e limites temporais resolvidos ficam congelados durante a execução.
- Retry de requisição: até cinco tentativas por página, com backoff exponencial e jitter; respeitar `Retry-After`.
- Persistir a próxima tentativa e liberar a posse durante a espera.
- Contadores de progresso refletem páginas confirmadas; tentativas HTTP são métricas separadas.
- Página estruturalmente inválida não avança cursor.
- Rejeição individual só permite avanço após quarentena confirmada; resultado final fica parcial.
- Cursor repetido ou sem avanço interrompe o job com erro explícito.
- Continuação acrescenta outro orçamento de até 2.000 registros ao mesmo percurso.
- Retomada manual registra novo ciclo de tentativas, preservando o histórico.
- Cursor invalidado exige nova varredura vinculada à anterior, claramente identificada.
- Limite atingido permanece parcial até a exaustão da consulta ser confirmada.

**Concluir quando:**

- [x] Passar S4 localmente com PostgreSQL isolado e fonte HTTP simulada.
- [x] Interrupções antes e depois do commit não perdem nem duplicam efeitos locais.
- [x] 429, timeout/rede, 5xx, 401/403, erro contratual e falha de banco seguem políticas distintas.
- [x] Reinício de serviço preserva agendamento, cooldown e checkpoint.
- [x] Consulta real multipágina permanece explicitamente pendente até S2 liberar o modo real.

**Demonstração:** coleta interrompida, retomada e comparada à execução sem interrupção.

### M5 — API operacional e contrato frontend

**Dependência:** M4.

**Implementar:**

- `/api/v1/jobs`: criar, listar, detalhar, cancelar, retomar e continuar.
- `/api/v1/processes`: listar, detalhar representações e movimentos.
- Consulta de presets habilitados e informações do ambiente.
- Criação de job com HTTP 202 e identificador persistido.
- Ação incompatível com estado retorna conflito explícito.
- Paginação local por página/tamanho, com ordenação estável; não expor cursor remoto como paginação de UI.
- DTOs de resposta independentes dos modelos ORM.
- Exportação OpenAPI e geração de tipos TypeScript com verificação em CI.

**Concluir quando:**

- API inicia uma coleta e permite consultar seus resultados.
- GETs não alteram dados de domínio.
- Jobs equivalentes simultâneos retornam a execução ativa, sem duplicá-la.
- Erros, estados parciais e totais desconhecidos têm contratos testados.

**Demonstração:** iniciar e acompanhar uma coleta exclusivamente por HTTP.

**Estado em 09/10/2026 — DONE localmente (SPEC-011):** os endpoints de jobs, processos, representações, movimentos, presets e ambiente estão implementados com DTOs tipados, erros uniformes e paginação local. Smoke Compose isolado confirmou readiness/OpenAPI HTTP 200, criação HTTP 202, processamento até `completed` pelo worker e leitura de processos, representações e movimentos. A fonte real continua desabilitada até aprovação de S1/S2 e evidências de S5. `npm ci`, `pytest tests/test_api.py` (10 testes), a suíte completa (201 testes), Ruff, formatação, mypy e `npm run openapi:check` passaram. Schema e tipos também coincidiram byte a byte com a geração em Python 3.14.8. A suíte reportou um aviso de depreciação Starlette/HTTPX.

### M6 — Primeira fatia completa de frontend

**Dependência:** M5.

**Implementar:**

- React/Vite, Router, TanStack Query, Tailwind e shadcn/ui.
- Shell navegável e identificação do ambiente.
- Radar mínimo, lista/detalhe de jobs e lista/detalhe de processos.
- Timeline preservando origem.
- Polling a cada três segundos para jobs ativos, encerrado em estado terminal.
- Carregamento, erro, vazio e parcial distintos.
- Primeiro teste de navegador usando dados sintéticos.

**Concluir quando:**

- Operador inicia, acompanha e abre resultados pela interface.
- Recarregar a página preserva o contexto consultável do job.
- Indisponibilidade não aparece como ausência de processos.
- Typecheck, build e fluxo de navegador passam.

**Demonstração:** primeira jornada visual completa, ainda sem dashboard.

**Estado em 09/10/2026 — DONE localmente (SPEC-012):** a aplicação React/Vite consome o contrato OpenAPI por `openapi-fetch` e tipos gerados. Ela entrega radar com presets versionados e motivos de indisponibilidade, lista/detalhe de coletas com polling de 3 s e comandos confirmados, e lista/detalhe de processos com capas por origem e timeline de três datas. Filtros e páginas ficam na URL, e o cache é segregado por ambiente. A API passou a aceitar CORS somente de `FRONTEND_ORIGIN` (loopback). Lint, typecheck, 39 testes Vitest, build, 214 testes backend em PostgreSQL isolado e 8 cenários Playwright (duas rodadas) passaram em stack `agrojud-e2e` efêmera. Falhas foram simuladas no navegador porque a fonte demo não falha; isso não valida o TJGO. Evidências em [SPEC-012](specs/SPEC-012-frontend-de-coleta-e-consulta.md).

### M7 — Triagem e classificação explicável

**Dependência:** M6; S5 para regras reais.

**Implementar em duas entregas:**

1. [x] Triagem, vínculo rural, notas e histórico — SPEC-013 concluída localmente em 09/10/2026 (222 testes backend, 42 frontend, 9 cenários Playwright).
2. [x] Regras versionadas, sinais e reprocessamento local — SPEC-014 concluída localmente em 09/10/2026 (227 testes backend, 44 frontend e 10 cenários Playwright); regras reais continuam desabilitadas enquanto S5 não tiver evidência validada.

**Interfaces adicionais:**

- Atualização de triagem com controle de versão para detectar edição desatualizada.
- Consulta do histórico.
- Comando de reprocessamento assíncrono pela mesma infraestrutura de jobs.

**Concluir quando:**

- Recoleta não altera decisões humanas.
- Confirmar vínculo rural exige justificativa.
- Todo sinal aponta para regra, versão e evidência.
- Reprocessar não consulta DataJud nem multiplica sinais equivalentes.
- Regras substituídas preservam histórico, distinguindo resultados atuais.

**Demonstração:** SPEC-013 comprovou revisão, reversão, histórico e preservação da decisão após recoleta. SPEC-014 comprovou sinais determinísticos somente em fixtures locais, publicação consistente por processo, retomada sem duplicação e evidência clicável. M7 está concluído localmente; isso não habilita regra real nem encerra S5.

### M8 — Acompanhamento, novidades e atualização diária

**Dependência:** M7.

**Implementar em três entregas:**

1. [x] Lista de acompanhados e atualização manual por número — SPEC-015 concluída localmente em 09/10/2026; estado, histórico e resultados de consulta persistidos, com update por processo acompanhado e UI validada.
2. [x] Referência histórica e caixa de novidades — SPEC-016 concluída localmente em 09/10/2026; baseline completa/pendente por representação, novidades com revisão persistida e replay/reprocessamento idempotentes.
3. [x] Agendamento persistente — SPEC-017 concluída localmente em 09/10/2026; busca versionada, execução manual, recuperação diária por alvo, acompanhamentos agendados e alternância justa da fila validados.

M8 está concluído localmente. A automação usa dados sintéticos; os gates de fonte real S5/S1/S2 permanecem externos.

**Definições:**

- Ao acompanhar um processo, usar como referência o histórico já conhecido.
- Primeira captura de nova representação estabelece sua referência; sinalizar a nova representação sem transformar todo seu histórico em novas ocorrências jurídicas.
- Alterações ambíguas recebem indicação de alteração observada.
- Reaparecimento de conteúdo já conhecido não recria novidade.
- Um job por número acompanhado e por busca salva; sem consultas remotas gigantes.
- Agendamento diário às 06h em `America/Sao_Paulo`, enquanto o ambiente estiver disponível.
- Reinício agrega disparos vencidos em uma atualização por alvo.
- Nova execução diária resolve novamente a janela relativa de 12 meses; retomada mantém a janela original.
- Atualizações de acompanhados precedem descobertas amplas, com alternância para não bloquear indefinidamente o radar.

**Concluir quando:**

- Histórico inicial não inunda a caixa de novidades.
- Movimento antigo recém-descoberto mantém as duas datas.
- Consulta vazia preserva dados anteriores.
- Disparo manual e agendado não duplicam trabalho equivalente.
- Reinício não cria uma execução por dia perdido.

**Demonstração:** SPEC-015 comprovou inclusão, atualização por número, resultado da consulta e remoção na stack demo isolada. SPEC-016 comprovou baseline pendente após resposta parcial, incorporação silenciosa da primeira versão completa, uma novidade por nova representação, distinção entre data do evento e primeira observação, revisão/reabertura, idempotência em replay/reprocessamento/reinclusão e rollback por falha/perda de posse. SPEC-017 comprovou revisões append-only, recuperação agregada com um dispatch por alvo, deduplicação de execução manual/agendada, adiamento após alteração durante job ativo, janela rolling congelada por coleta, gate para DataJud real e alternância persistida entre refresh e discovery. Migration 0010 e testes de aceitação passaram em PostgreSQL isolado; a validação desta entrega registrou 261 testes backend, 49 frontend, lint, typecheck, mypy, build e 13 cenários Playwright aprovados. A fonte permanece sintética e não valida filtros nem respostas reais do DataJud/TJGO.

### M9 — Indicadores, CSV e aceite do MVP

**Dependência:** M8.

**Estado local:** SPEC-018 implementada e validada em 09/10/2026. Exportação (SPEC-019), operação integrada e aceite real (SPEC-020) permanecem pendentes; M9 não está concluído.

**Validação da SPEC-018:** 266 testes backend, 54 frontend e 14 cenários Playwright aprovados; Ruff, formatação, mypy, ESLint, typecheck, build e OpenAPI aprovados. Planos das quatro consultas foram inspecionados com `EXPLAIN ANALYZE` em amostra de 102 processos/103 representações. A evidência detalhada e os limites locais estão na [SPEC-018](specs/SPEC-018-indicadores-da-base-local.md); isso não altera os gates externos DataJud/TJGO.

**Implementar:**

- [x] Contadores locais e distribuições simples — SPEC-018 DONE localmente, com amostra sintética.
- [ ] CSV reutilizando os filtros da consulta de processos.
- [ ] Neutralização de fórmulas, encoding e escape testados.
- [ ] Compose completo, incluindo frontend.
- [ ] Procedimentos de backup/restauração e reset somente do ambiente demonstrativo.
- [ ] README, arquitetura, operação de jobs e roteiro de demonstração.
- [ ] Medição de uma coleta controlada: duração, páginas, retries, volume e crescimento do banco.

**Concluir quando:**

- Indicadores não duplicam processos por junções com assuntos/movimentos.
- Exportação corresponde aos filtros e identifica a origem dos dados.
- Instalação a partir de checkout limpo funciona.
- Backup restaurado preserva processos, triagem e acompanhamento.
- Testes unitários, PostgreSQL, contratos, frontend e navegador passam.
- Aceite real registra evidências específicas de TJGO e paginação; não pode ser substituído pelo aceite sintético.

**Demonstração final:** coletar → interromper → recuperar → revisar → acompanhar → atualizar → exportar.

## 6. Validação transversal e riscos

Cada milestone adiciona seus testes junto à implementação. Não concentrar testes no fim.

| Risco | Tratamento e critério |
| --- | --- |
| Fonte indisponível | Adaptador sintético independente; aceite real pendente e visível. |
| Ordenação não validada | Bloquear declaração de paginação real confiável; não usar apenas timestamp por conveniência. |
| Mutação entre páginas | Revarreduras identificadas e idempotentes; não prometer snapshot consistente. |
| Fusão incorreta de movimentos | Preservar multiplicidade, versões e incerteza; executar S3 antes de novidades. |
| Worker com posse expirada | Verificação transacional do token e lease em toda confirmação. |
| Falha de banco | Rollback da página e checkpoint; recuperação sem perda do último ponto confirmado. |
| Crescimento de execuções amplas | Orçamento por execução, parcial explícito e continuação manual. |
| Testes interferindo entre si | Banco isolado, execução serial inicial e ausência de limpeza no banco operacional. |
| Complexidade de infraestrutura | Uma imagem backend para API/worker; nenhuma fila externa. |
| Escopo visual consumindo o projeto | Interface começa em M6; gráficos somente após M8. |

Usar índices direcionados às consultas e à fila ativa, com restrições de unicidade no banco. Não criar índices JSONB genéricos sem necessidade demonstrada. Referência: [Índices parciais no PostgreSQL](https://www.postgresql.org/docs/current/indexes-partial.html).

## 7. Deferred Work

**Adiado até o núcleo estar validado, mas pertencente ao MVP:**

- Atualização diária: M8.
- Indicadores e CSV: M9.
- Acabamento visual final: M9.

**Fora do MVP:**

- Múltiplos tribunais, usuários, permissões e exposição pública.
- Redis, Celery, RabbitMQ e múltiplos workers operacionais.
- WebSocket/SSE e notificações externas.
- XLSX, dashboards extensos e construtor genérico de consultas.
- IA, documentos, embeddings e pesquisa por partes.
- Reconciliação jurídica automática de eventos ambíguos.
- Purga automática de histórico e infraestrutura externa de métricas.
- Controle de prazos, prognósticos jurídicos e CRM.

Não criar abstrações antecipadas para esses itens. A interface de fonte e os contratos de domínio bastam para preservar possibilidades futuras.

## 8. Checklist geral

- [x] Ler integralmente o PRD.
- [x] Inspecionar estado atual do repositório.
- [x] Materializar este conteúdo em `IMPLEMENTATION_PLAN.md`.
- [x] M0 — Fundação e PostgreSQL executáveis (SPEC-001 DONE).
- [ ] S1 — Contrato real TJGO verificado.
- [ ] S2 — Paginação real verificada.
- [x] S3 — Reconciliação documentada e testada (SPEC-004 DONE; fixtures sintéticas, sem validação da fonte real).
- [ ] M1 — Adaptadores e erros tipados.
- [x] M2 — Persistência de página idempotente (SPEC-005 e SPEC-006 concluídas localmente).
- [x] M3 — Fila, lease e posse (SPEC-007 DONE localmente).
- [x] SPEC-008 — Paginação e checkpoints localmente concluídos; fonte real permanece bloqueada por S2.
- [x] S4 — Atomicidade e recuperação comprovadas localmente; integração real permanece bloqueada por S2.
- [x] M4 — Coleta recuperável demonstrada localmente; consulta real multipágina segue pendente.
- [x] SPEC-010 — Catálogo versionado e investigação TPU concluídos localmente; gates de evidência real preservados.
- [x] M5 — API e OpenAPI estáveis (SPEC-011 DONE localmente; fonte real continua bloqueada por evidência externa).
- [x] M6 — Fluxo visual completo (SPEC-012 DONE localmente; dados demo sintéticos, fonte real segue bloqueada).
- [ ] S5 — Catálogo temático validado.
- [x] M7 — Triagem e regras concluídas localmente (SPEC-013/014); habilitação real continua condicionada a S5.
- [x] M8 — Acompanhamento, novidades e agendamento estão concluídos localmente (SPEC-015/016/017); fonte real continua condicionada a S5/S1/S2.
- [x] SPEC-018 — Indicadores da base local concluídos localmente; amostra sintética e não representativa do universo TJGO.
- [ ] M9 — Indicadores, CSV e operação documentada.
- [ ] Aceite sintético completo.
- [ ] Aceite real TJGO registrado separadamente.
- [ ] Instalação limpa e restauração verificadas.
- [ ] Limitações remanescentes documentadas.

## 9. Divisão final em SPECs

A decomposição aprovada está em [specs/README.md](specs/README.md). São 20 unidades implementáveis. SPEC-001 a SPEC-018 estão DONE localmente; SPEC-019 e SPEC-020 permanecem pendentes. A conclusão de uma SPEC não promove sucessoras nem valida a fonte automaticamente.

| Unidade | Milestone/Spike | Entrega | Dependências diretas |
| --- | --- | --- | --- |
| [SPEC-001](specs/SPEC-001-fundacao-local.md) | M0 | Fundação local | Nenhuma |
| [SPEC-002](specs/SPEC-002-contratos-e-adaptadores-de-fonte.md) | M1 | Contratos e adaptadores de fonte | SPEC-001 |
| [SPEC-003](specs/SPEC-003-validacao-datajud-tjgo.md) | S1/S2 | Validação DataJud/TJGO | SPEC-002 |
| [SPEC-004](specs/SPEC-004-identidade-e-reconciliacao.md) | S3 | Identidade e reconciliação | SPEC-002 |
| [SPEC-005](specs/SPEC-005-persistencia-de-capas-e-payloads.md) | M2 | Persistência de capas e payloads | SPEC-002 |
| [SPEC-006](specs/SPEC-006-movimentos-e-quarentena.md) | M2 | Movimentos e quarentena | SPEC-004, SPEC-005 |
| [SPEC-007](specs/SPEC-007-jobs-leases-e-posse.md) | M3 | Jobs, leases e posse | SPEC-006 |
| [SPEC-008](specs/SPEC-008-paginacao-e-checkpoints.md) | M4 | Paginação e checkpoints | SPEC-007 |
| [SPEC-009](specs/SPEC-009-retries-e-recuperacao.md) | M4/S4 | Retries e recuperação | SPEC-008 |
| [SPEC-010](specs/SPEC-010-catalogo-tematico-versionado.md) | S5/M1 | Catálogo temático versionado | SPEC-002 |
| [SPEC-011](specs/SPEC-011-api-operacional-e-openapi.md) | M5 | API operacional e OpenAPI | SPEC-009, SPEC-010 |
| [SPEC-012](specs/SPEC-012-frontend-de-coleta-e-consulta.md) | M6 | Frontend de coleta e consulta | SPEC-011 |
| [SPEC-013](specs/SPEC-013-triagem-e-historico-humano.md) | M7 | Triagem e histórico humano | SPEC-012 |
| [SPEC-014](specs/SPEC-014-sinais-e-reprocessamento-local.md) | M7 | Sinais e reprocessamento local | SPEC-013 |
| [SPEC-015](specs/SPEC-015-acompanhamento-manual.md) | M8 | Acompanhamento manual | SPEC-014 |
| [SPEC-016](specs/SPEC-016-referencia-historica-e-novidades.md) | M8 | Referência histórica e novidades | SPEC-015 |
| [SPEC-017](specs/SPEC-017-buscas-salvas-e-agendamento.md) | M8 | Buscas salvas e agendamento | SPEC-016 |
| [SPEC-018](specs/SPEC-018-indicadores-da-base-local.md) | M9 | Indicadores da base local | SPEC-017 |
| [SPEC-019](specs/SPEC-019-exportacao-csv.md) | M9 | Exportação CSV | SPEC-017 |
| [SPEC-020](specs/SPEC-020-operacao-e-aceite-integrado.md) | M9 | Operação e aceite integrado | SPEC-018, SPEC-019 |

Ordem recomendada: 001 → 002 → 003 → 004 → 005 → 006 → 007 → 008 → 009 → 010 → 011 → 012 → 013 → 014 → 015 → 016 → 017 → 018 → 019 → 020. SPEC-010 pode ser antecipada após 002; SPEC-018 e SPEC-019 são independentes após 017.

### Esclarecimentos técnicos registrados na especificação

1. **M2 sem dependência circular de M3:** a coleta recebe identidade própria em SPEC-005; jobs se vinculam a ela em SPEC-007. Observações não exigem job fictício.
2. **Baseline histórica:** só se estabelece por representação com lista de movimentos presente e normalizada sem rejeição. Snapshot parcial mantém referência pendente; nova representação produz descoberta de representação, não um alerta por evento histórico.
3. **Identidade técnica:** SPEC-004 fixa comparação por conteúdo e multiplicidade, com alterações observadas sem afirmar novo ato. Reordenação do bruto pode criar versão bruta distinta sem criar movimento novo.
4. **Validação real por capacidade:** SPEC-003 separa resultado do probe de aprovação S1/S2; SPEC-010 separa entrega do catálogo de aprovação de presets/regras. Indisponibilidade não impede o núcleo sintético, mas bloqueia habilitação e aceite reais.
5. **Reprocessamento e continuação:** quarentena tem resolução local auditada; continuar paginação não resolve rejeições nem reescreve cobertura histórica. Regras têm reprocessamento próprio sem HTTP.
6. **Cancelamento e posse:** cancelamento é serializado com commit; página já confirmada é preservada. Após cancelamento confirmado, nenhuma nova página pode confirmar.
7. **Defaults operacionais:** SPEC-002 fixa timeouts HTTP (connect 5s, read/write 20s, pool 5s); SPEC-007 mantém lease 120s/heartbeat 20s e limita transações (statement 30s, lock 5s); SPEC-009 fixa espaçamento 1s, backoff com jitter até 60s e orçamentos de recuperação. São parâmetros configuráveis locais, não limites oficiais DataJud.
8. **Exportação síncrona:** SPEC-019 acrescenta limite configurável de 50.000 processos, retorno explícito de excesso e geração temporária antes do download para evitar CSV truncado apresentado como sucesso. Não modifica o limite de 2.000 hits por coleta.
9. **Aceite final:** SPEC-020 permanece BLOCKED_VALIDATION se faltarem evidências reais exigidas, mesmo com demonstração sintética aprovada.

Esses pontos detalham ambiguidades do plano; não ampliam o domínio funcional nem alteram o PRD. A checklist de milestones permanece pendente até implementação e evidência correspondentes.

