# SPEC-025 — Suporte multitribunal no produto

Status: REFORMULADA — implementação conforme este escopo pendente.

Data: 12/10/2026

Milestone: Pós-M9 / suporte multitribunal

Dependências: [SPEC-002 — Contratos e adaptadores de fonte](SPEC-002-contratos-e-adaptadores-de-fonte.md), [SPEC-004 — Identidade e reconciliação](SPEC-004-identidade-e-reconciliacao.md), [SPEC-005 — Persistência de capas e payloads](SPEC-005-persistencia-de-capas-e-payloads.md), [SPEC-006 — Movimentos e quarentena](SPEC-006-movimentos-e-quarentena.md), [SPEC-007 — Jobs, leases e posse](SPEC-007-jobs-leases-e-posse.md), [SPEC-008 — Paginação e checkpoints](SPEC-008-paginacao-e-checkpoints.md), [SPEC-009 — Retries e recuperação](SPEC-009-retries-e-recuperacao.md), [SPEC-010 — Catálogo temático versionado](SPEC-010-catalogo-tematico-versionado.md), [SPEC-011 — API operacional e OpenAPI](SPEC-011-api-operacional-e-openapi.md), [SPEC-012 — Frontend de coleta e consulta](SPEC-012-frontend-de-coleta-e-consulta.md), [SPEC-015 — Acompanhamento manual](SPEC-015-acompanhamento-manual.md), [SPEC-016 — Referência histórica e novidades](SPEC-016-referencia-historica-e-novidades.md), [SPEC-017 — Buscas salvas e agendamento](SPEC-017-buscas-salvas-e-agendamento.md), [SPEC-018 — Indicadores da base local](SPEC-018-indicadores-da-base-local.md) e [SPEC-019 — Exportação CSV](SPEC-019-exportacao-csv.md).

Referência técnica: [SPEC-024 — Validação multitribunal do DataJud](SPEC-024-validacao-multitribunal-datajud.md). A ferramenta, a matriz e seus resultados permanecem como evidência técnica e documentação; não são dependência de runtime nem critério de habilitação de tribunal nesta SPEC.

## Objetivo

Permitir que a pessoa usuária faça uma coleta no Radar em um, vários ou todos os tribunais atendidos pela allowlist DataJud já existente, com a mesma chave pública configurada para TJGO. A seleção nacional usa os 33 endpoints allowlisted: TJs estaduais, TJDFT e TRF1 a TRF6.

Cada tribunal terá execução e resultado independentes. A falha de um membro não impede a consulta nem remove os resultados dos demais. TJGO continua disponível pela mesma jornada e pelos contratos legados.

## Comportamento e decisões

- O Radar lista os 33 tribunais da allowlist e permite selecionar um, vários ou todos. Na primeira abertura, **Todos** vem selecionado; a pessoa pode desmarcar tribunais ou usar os controles de seleção para iniciar uma busca menor.
- A opção Todos representa uma coleta nacional nos 33 endpoints existentes. A interface mostra com clareza a quantidade e o escopo selecionado antes do envio.
- Cada tribunal selecionado recebe um job de coleta existente, uma collection própria e seu checkpoint independente. Um grupo existente agrega a solicitação e o progresso, sem substituir a fila, o worker, a paginação, as leases ou a ingestão atuais.
- Mesmo uma seleção de um único tribunal mantém o vínculo individual entre grupo, membro e job. A rota legada de job único e seus snapshots sem tribunal continuam interpretando a origem como TJGO.
- Consultas salvas persistem a lista escolhida na revisão. Revisões antigas sem a lista continuam sendo lidas como TJGO e não são reescritas. Atualizações de acompanhamentos consultam, de forma independente, os tribunais das representações já conhecidas.
- Filtros de Processos, indicadores, drill-downs e CSV respeitam a seleção de tribunal e mantêm uma linha/contagem por CNJ onde hoje esse é o contrato.

## DataJud e configuração

- Para cada sigla, o backend resolve somente o endpoint correspondente já presente em `TRIBUNAL_ALLOWLIST`, no formato `https://api-publica.datajud.cnj.jus.br/api_publica_{alias}/_search`.
- Todos os endpoints usam a mesma configuração existente `DATAJUD_API_KEY`, enviada pelo backend/worker no cabeçalho `Authorization: APIKey ...`. Nenhuma chave por tribunal é necessária.
- Remover do runtime a exigência de matriz, hash promovido, feature flag multitribunal e montagem de arquivo de matriz. Nenhuma variável nova será introduzida para liberar tribunais.
- Presets e filtros atuais são enviados em cada endpoint pelo adaptador existente. A disponibilidade de uma combinação não depende de S1/S2, S5 ou estado de revisão da matriz.
- A allowlist continua sendo o limite de entrada: siglas/aliases desconhecidos são recusados antes de enfileirar; nenhuma URL é aceita diretamente da pessoa usuária.

## Contratos e resultado parcial

- Reutilizar `GET /api/v1/tribunal-availability` para apresentar os itens da allowlist sem bloqueio por preset ou matriz. Se o parâmetro `preset_id` for mantido para compatibilidade, ele não altera a lista permitida.
- Reutilizar `POST /api/v1/job-groups` com a lista explícita `tribunals`, inclusive para uma única sigla, e `GET /api/v1/job-groups/{group_id}` para acompanhar o resultado de cada membro. Não criar serviço externo ou fila nova.
- Cada membro expõe estado, cobertura, checkpoint e erro próprios. O grupo informa conclusão parcial quando ao menos um membro concluir e outro falhar, ficar parcial ou ainda estiver pendente, segundo os estados de execução já adotados.
- Timeout, HTTP 401/403/429/5xx, resposta inválida/incompleta, cursor inválido ou divergência de identidade permanecem erro ou resultado parcial do tribunal correspondente. Nunca converter esses casos em lista vazia ou sucesso.
- Retry e cooldown usam os mecanismos atuais e afetam apenas o membro correspondente. Página confirmada e checkpoint permanecem preservados conforme as regras de posse e atomicidade existentes.
- A resposta válida com zero processos continua sendo zero somente quando a fonte conclui a consulta com resposta válida. Isso é distinto de erro ou ausência de resposta.

Campos opcionais de hash/versão de matriz já persistidos podem permanecer para leitura de grupos antigos. Novos jobs não dependem desses campos, e não se cria migration apenas para apagar metadados históricos.

## Critérios de aceite

- **AC1 — allowlist:** Radar e backend apresentam exatamente os 33 tribunais da allowlist atual, incluindo TJGO, os demais TJs/TJDFT e TRF1–TRF6; entrada fora da allowlist é recusada.
- **AC2 — seleção simples:** interface permite um, vários e todos; Todos inicia marcado e inclui os 33 tribunais na requisição.
- **AC3 — mesma credencial:** cada membro usa a chave DataJud já existente no backend e o alias correto. Nenhum segredo aparece em frontend, fixtures públicas, logs ou Git.
- **AC4 — execução independente:** cada tribunal selecionado tem job, collection, cobertura e checkpoint próprios, sem transação aberta durante HTTP.
- **AC5 — falha parcial:** falha simulada em um endpoint não interrompe os demais; resultados confirmados permanecem acessíveis e grupo apresenta estado parcial e motivo por membro.
- **AC6 — falha nunca é vazio:** timeout, 401/403, 429, 5xx e resposta inválida/incompleta são distintos de consulta válida sem hits e não são apresentados como zero resultados.
- **AC7 — compatibilidade:** coleta legada sem tribunal continua usando TJGO; buscas salvas antigas sem lista continuam legíveis; decisões humanas, representações, jobs, collections e checkpoints históricos são preservados.
- **AC8 — produto consistente:** buscas salvas/agendadas e acompanhamentos mantêm a seleção/origens definidas pelo seu fluxo; filtros, indicadores e CSV não duplicam processo CNJ por ter múltiplas representações.
- **AC9 — operação simples:** API, worker e Compose não exigem arquivo/hash de matriz nem feature flag para usar os 33 endpoints. A chave existente é suficiente como configuração específica do DataJud.
- **AC10 — validação:** testes automatizados cobrem allowlist, seleção unitária/múltipla/nacional, independência, falha parcial, compatibilidade e UI. OpenAPI/tipos, lint, typecheck, build e verificações pertinentes de banco/containers passam antes da conclusão da SPEC.

## Validação e evidência

- Testes locais usam PostgreSQL isolado e HTTP simulado; não alteram dados operacionais e não são apresentados como prova de disponibilidade do CNJ.
- Probes externas, quando executadas, são limitadas, somente de leitura, separadas dos testes e documentadas por endpoint, horário, status e classificação. Não persistem resultados operacionais.
- Uma falha externa pontual deve ser registrada como indisponibilidade daquele endpoint, sem voltar a bloquear os demais por gate de matriz. O runtime segue tentando as consultas selecionadas e informa os erros individualmente.
- A matriz da SPEC-024 permanece disponível para consulta técnica e evolução da evidência. Seu status parcial, hash ou decisão humana não habilitam nem desabilitam consultas no produto.
- Esta revisão documental não conclui a SPEC. O status só muda para DONE depois da implementação e da aprovação de todos os critérios de aceite desta versão.

## Fora de escopo

- Ampliar a allowlist além dos 33 endpoints atuais ou adicionar TRT, TRE, tribunal superior ou tribunal militar.
- Criar chave DataJud por tribunal, uma nova infraestrutura de fila, worker/orquestrador ou framework genérico de provedores.
- Usar a matriz da SPEC-024 como gate, promover hash de matriz ou impedir consultas por ausência de tentativa/aprovação técnica.
- Alterar identidade CNJ, semântica de ingestão, decisões humanas ou mecanismo existente de leases, retry, paginação e checkpoints.
