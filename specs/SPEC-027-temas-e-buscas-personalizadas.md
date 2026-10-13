# SPEC-027 — Temas oficiais e buscas personalizadas

Status: DONE — implementação e aceite local em 13/10/2026; sem probe DataJud nesta unidade.

Milestone/Spike: Pós-M9 / produto jurídico e buscas estruturadas

## Dependências

[SPEC-010](SPEC-010-catalogo-tematico-versionado.md),
[SPEC-011](SPEC-011-api-operacional-e-openapi.md),
[SPEC-012](SPEC-012-frontend-de-coleta-e-consulta.md),
[SPEC-017](SPEC-017-buscas-salvas-e-agendamento.md),
[SPEC-025](SPEC-025-suporte-multitribunal.md) e
[SPEC-026](SPEC-026-eficiencia-coleta-multitribunal.md).

Referências: [PRD](../PRD.md), [plano](../IMPLEMENTATION_PLAN.md) e evidência temática vigente em [`catalogo-tematico-tpu-2026-10-09.json`](../docs/evidence/catalogo-tematico-tpu-2026-10-09.json) e [`catalogo-tematico-datajud-2026-10-10.json`](../docs/evidence/catalogo-tematico-datajud-2026-10-10.json).

## Objetivo

Apresentar as consultas de descoberta como temas jurídicos compreensíveis e permitir que a pessoa defina, versione e arquive buscas próprias, preservando os contratos versionados, snapshots, coletas e explicações já existentes.

## Contexto e estado confirmado

O catálogo vigente no código é `1.2.0`, com versão TPU `06/10/2026`. A fonte de códigos é `tpu_code_evidence` em `backend/src/agrojud/sources/thematic_catalog.v1.json`, cuja pesquisa oficial está registrada nos relatórios vinculados acima. Esta SPEC usa somente os códigos ali existentes e aprovados; a seleção personalizada exige também `situation = A`, aplicabilidade TJGO e `selectable_for_custom = true`.

O conjunto atual aprovado para crédito rural contém assuntos `4964` — Cédula de Crédito Rural, `4976` — Nota de Crédito Rural e `10501` — Crédito Rural. Não adicionar o código `4968` por menções históricas em documentos. Para recuperação judicial, a definição atual usa classe `129` ou assunto `13277`. Classe `108` e assunto `4993` continuam apenas nas definições históricas compatíveis e não podem ser selecionados em novas buscas. A lista de classes de execução e os assuntos rurais abaixo também vêm exclusivamente do catálogo versionado.

As evidências do catálogo mantêm a distinção entre situação TPU, consulta S5 e amostra. A consulta combinada de execuções relacionadas ao agro permanece inconclusiva para ativação real; o tema deve ficar indisponível no modo real enquanto suas evidências exigidas não forem aprovadas. A compilação local e as fixtures sintéticas não alteram esse gate nem comprovam disponibilidade externa.

## Escopo

- Três temas oficiais, versionados e somente leitura para a pessoa usuária.
- Criação de busca personalizada vazia ou por duplicação de um tema oficial.
- Edição append-only, consulta do histórico e arquivamento de buscas personalizadas.
- Seleção de classes e assuntos através do catálogo TPU conhecido pelo AgroJud.
- Compilação de critérios estruturados para filtros DataJud allowlisted.
- Entrada tipada no Radar, buscas salvas e grupos multitribunal.
- Explicação dos códigos estruturados que motivaram cada inclusão, agregada por representação/CNJ.
- Compatibilidade de leitura e execução com IDs e snapshots já persistidos.

Fora do escopo: query Elasticsearch/JSON livre, editor genérico de query, edição de tema oficial, novas regras por texto, novos códigos TPU, novos mecanismos de fila ou alterações de leases, paginação, retries, checkpoints, rate limit, deduplicação por CNJ ou representações por origem.

## Temas oficiais

O Radar apresenta uma seção **Temas do sistema** com as opções abaixo. Cada definição é versionada no catálogo e oferece **Usar**, **Visualizar critérios** e **Duplicar e personalizar**. Os IDs técnicos continuam internos à API e aos snapshots.

| Tema da busca | ID interno e versão | Critérios efetivos | Combinação entre dimensões | Evidência/limite |
| --- | --- | --- | --- | --- |
| Crédito e contratos rurais | `tema.credito_contratos_rurais` `1.0.0` | assuntos `4964`, `4976`, `10501` | OR dentro de assunto; sem classe | Somente os três assuntos ativos aprovados no catálogo. |
| Recuperação judicial | `tema.recuperacao_judicial` `1.0.0` | classe `129` ou assunto `13277` | OR entre classe e assunto; OR dentro de cada dimensão | Uma consulta DataJud com `bool.should` e `minimum_should_match: 1`; não duplica jobs por estratégia. Códigos `108`/`4993` são legados. Recuperação judicial, por si, não confirma vínculo rural. |
| Execuções relacionadas ao agro | `tema.execucoes_relacionadas_ao_agro` `1.0.0` | classes `159`, `12154`, `1116`, `155`, `156`, `12078`, `12079` **e** assuntos rurais `4964`, `4976`, `10501` | OR dentro das dimensões; AND entre classe e assunto | Classe de execução sem assunto rural não basta. O tema permanece sujeito ao gate S5 vigente. |

`execucoes.amplas`, `rural.credito_contratos`, `recuperacao_judicial.por_classe` e `recuperacao_judicial.por_assunto` continuam presentes como definições legadas. `execucoes.amplas` não aparece como opção primária do Radar; pode permanecer disponível somente nos contratos internos/históricos existentes. O catálogo não combina a classe genérica sozinha com uma inferência textual para ampliar resultados.

## Buscas personalizadas

Em **Minhas buscas**, a pessoa pode criar uma busca do zero ou duplicar um tema oficial. A definição editável contém:

- nome obrigatório e descrição opcional;
- classes TPU e assuntos TPU selecionados por código e nome;
- combinação `AND` ou `OR` entre dimensões não vazias; valores dentro de cada dimensão usam OR;
- sem query livre, campo oculto de DSL ou inferência textual.

Tribunais, período de ajuizamento, tamanho de página e limite opcional continuam nos parâmetros de execução e de busca salva existentes. Eles não alteram a definição temática. Códigos digitados fora do catálogo conhecido, inativos, sem dimensão ou sem aprovação explícita são rejeitados; esta versão não oferece inclusão manual fora do catálogo.

### Edição, histórico e arquivamento

Editar uma busca cria a próxima versão imutável. A versão anterior não muda e coletas, jobs, snapshots e buscas salvas permanecem associados aos critérios usados. O endpoint de histórico expõe revisões anteriores para auditoria. Arquivar retira a busca das seleções novas, mantém suas versões e permite que buscas salvas e snapshots históricos continuem legíveis e executáveis pela definição congelada. Não há exclusão destrutiva de busca com histórico.

## Consulta e explicabilidade

O compilador recebe uma definição tipada, valida cada código contra o catálogo e produz apenas filtros allowlisted para a API DataJud. Em uma única dimensão, os códigos são unidos por OR. Com duas dimensões, AND gera filtros separados dentro do filtro externo; OR gera um único `bool.should` com mínimo de uma correspondência. Datas, tribunal e outros filtros continuam no nível externo da consulta, sem mudar essa relação.

O snapshot da coleta preserva catálogo e versão da definição, códigos efetivos, operadores, nomes e critérios explicativos. Para cada representação, a API compara os critérios congelados com os códigos de classe e assuntos estruturados do payload original. Uma única dimensão gera, por exemplo, `Assunto 10501 — Crédito Rural`; correspondências em ambas as dimensões são consolidadas em uma explicação combinada. Se o payload não preservou o código correspondente, a resposta identifica o critério consultado sem alegar correspondência observada. Não se usa texto livre para inferir vínculo rural.

Um CNJ continua sendo um processo deduplicado, enquanto representações por origem permanecem independentes. Se o mesmo CNJ for encontrado por mais de um componente/consulta, a API consolida os motivos nas evidências da representação e não cria um segundo processo por estratégia. Para recuperação judicial, classe e assunto são componentes internos de uma mesma busca e uma query; quando o payload contém ambos, ambos aparecem na explicação.

## Compatibilidade e versionamento

- IDs antigos e definições catalogadas permanecem intactos; não há migration de rename nem atualização dos snapshots históricos.
- Jobs antigos continuam interpretando `preset_id`, `preset_version`, query e `catalog_snapshot` persistidos. Endpoints antigos de leitura mantêm os campos usados por clientes históricos.
- Novos jobs enviam `search_definition` tipado como tema oficial + versão textual ou busca personalizada + revisão inteira. O identificador de preset continua sendo usado internamente para caber nos snapshots e nas tabelas atuais.
- Buscas salvas novas mantêm no registro legado `preset_id`/`preset_version` por compatibilidade e acrescentam uma referência tipada na resposta/API. A revisão congelada guarda a definição e critérios usados. Referências personalizadas arquivadas permanecem utilizáveis apenas quando a revisão salva já contém o snapshot correspondente.
- Ao mudar um tema oficial no futuro, incrementar sua versão; revisões salvas com versão antiga não devem ser recompiladas silenciosamente com novos critérios.
- `rural.credito_contratos`, `execucoes.amplas`, `recuperacao_judicial.por_classe` e `recuperacao_judicial.por_assunto` são nomes históricos internos, não opções da decisão principal do Radar.

## Modelo de dados e contratos

O catálogo oficial continua em `thematic_catalog.v1.json`. A persistência adicional é limitada às tabelas `custom_searches` (identidade, versão corrente, arquivamento) e `custom_search_versions` (nome, descrição, códigos, operador, versão e timestamp), introduzidas pela migration `20261013_0012`. Constraints protegem nome, listas não vazias, versão e operador; trigger PostgreSQL impede alteração/exclusão de versões. A referência de tribunal e filtros de execução fica nos contratos já existentes.

Endpoints adicionados:

- `GET /api/v1/search-types` lista somente temas oficiais e disponibilidade por ambiente;
- `GET /api/v1/tpu-codes?dimension=class|subject&q=...` pesquisa códigos aprovados para novas buscas;
- `GET/POST/PATCH /api/v1/custom-searches` lista, cria e edita buscas;
- `GET /api/v1/custom-searches/{id}/versions` lê histórico;
- `POST /api/v1/custom-searches/{id}/archive` arquiva sem apagar;
- `POST /api/v1/jobs`, `/api/v1/job-groups` e `/api/v1/saved-searches` aceitam as referências tipadas além das referências legadas.

OpenAPI e tipos TypeScript continuam gerados a partir do schema FastAPI. A UI deve exibir “Tema da busca”, “Temas do sistema” e “Minhas buscas”; códigos e combinações só aparecem na visualização/configuração de critérios e na explicação do processo.

## Multitribunal

O contrato é **uma definição escolhida × tribunais escolhidos**. Para cada tribunal, a arquitetura existente mantém job, coleção, checkpoint, status e falha independentes. A estratégia interna de recuperação judicial não vira outro job por tribunal. Parâmetros salvos podem conter um ou vários tribunais; um run cria um grupo com membros independentes pelo mecanismo existente.

## Decisões arquiteturais

1. **Uma consulta unificada por tema de recuperação:** as evidências S5 individuais já registradas suportam a união por `bool.should`; isso mantém a semântica de classe OU assunto e evita fan-out de jobs. A consulta unificada continua sujeita à avaliação vigente antes de ativação real.
2. **Dois registros próprios e simples para personalização:** identidade mutável do preset separado de revisões imutáveis; evita alterar o schema histórico de buscas salvas e snapshots de coleta.
3. **Snapshot imutável como contrato de execução:** jobs e revisões salvas consultam a definição capturada no momento da execução; arquivar/editar a busca não reescreve histórico.
4. **Catálogo como única origem de códigos:** o endpoint e o compilador usam a mesma lista `selectable_for_custom`; nenhum código informado pelo cliente passa diretamente ao DataJud.
5. **Explicação baseada em estrutura observada:** só os códigos TPU do snapshot e do payload original sustentam motivos observados. Ausência de dado é explicada como critério consultado, sem inferência textual.
6. **Compatibilidade aditiva:** conservar IDs e campos antigos para leitura; novas ações usam referências tipadas. Não migrar ou renomear históricos.

## Critérios de aceitação

- [x] AC1: API e UI exibem exatamente os três temas oficiais aprovados, sem IDs ou detalhes de estratégia na decisão principal.
- [x] AC2: os temas compilam os códigos e operadores acima, sem introduzir ou reativar códigos TPU.
- [x] AC3: execução relacionada ao agro exige classe de execução e assunto rural válido; classe genérica isolada não vira evidência rural.
- [x] AC4: classe 129, assunto 13277 e combinação localizam recuperação judicial numa busca lógica, query e job por tribunal; mesmas representações não duplicam CNJ e motivos são unidos.
- [x] AC5: criação do zero e duplicação de tema criam busca estruturada; códigos inválidos são recusados e não existe campo de query livre.
- [x] AC6: edição grava nova versão, versões antigas permanecem imutáveis e arquivamento remove a busca da seleção nova sem destruir buscas/coletas históricas.
- [x] AC7: buscas salvas antigas e novas, snapshots e jobs históricos permanecem legíveis; novos jobs usam referências tipadas.
- [x] AC8: um e vários tribunais continuam com execuções e checkpoints independentes pelo grupo existente.
- [x] AC9: API/OpenAPI e tipos TypeScript coincidem; frontend e Playwright cobrem escolha, critérios, duplicação, edição e arquivamento.
- [x] AC10: fluxo de coleta existente, migração em PostgreSQL isolado, lint, formatação, typecheck e build passam; nenhuma política de retry/lease/paginação/checkpoint/rate limit ou deduplicação foi alterada.
- [x] AC11: documentação e diff final são revisados; aceite local não presume probe externa.

## Testes necessários

Cobrir catálogo/compilação dos três temas, código ativo e rejeição de códigos antigos/inativos/desconhecidos, AND/OR, payload DataJud allowlisted, explicações de classe/assunto/ambos, deduplicação por CNJ, custom create/duplicate/edit/version/archive, snapshots preservados, saved searches antigos/novos, um/vários tribunais, migração em banco PostgreSQL isolado, OpenAPI/TypeScript, testes React e Playwright. Reexecutar os testes existentes de coleta para identificar regressões. Testes usam PostgreSQL isolado, fonte sintética e HTTP simulado; não executar chamada real ao DataJud como parte desta SPEC.

## Erros e edge cases

Código desconhecido ou desativado: rejeitar com erro de validação. Tema ou revisão enviada com versão incompatível: rejeitar novas consultas e exigir atualização explícita da seleção. Busca personalizada arquivada: impedir novo uso, mas preservar execução de uma revisão salva que já congelou a definição. Resposta externa incompleta ou falha segue os contratos existentes e nunca é tratada como resultado vazio.

## Evidência e conclusão

Todos os critérios abaixo passaram em validação local. A suíte de backend foi executada em PostgreSQL isolado e a suíte Playwright em Chromium contra o Compose demo descartável, com fonte sintética e sem chave DataJud.

- Backend: `docker compose --project-name agrojud-spec027-test --env-file .env.test -f compose.test.yaml --profile test run --rm --build tests uv run --no-sync pytest -q` — 411 testes passaram. A migration foi exercitada em banco vazio e na atualização de `20261012_0011`, preservando uma linha histórica de processo. Ruff check/format, mypy (84 arquivos fonte) e compileall passaram. A suíte reportou um aviso de depreciação Starlette/HTTPX.
- Frontend: `npm run typecheck`, `npm run lint`, `npm run test -- --pool=forks --maxWorkers=2` (72 testes), `npm run build` e `UV_PROJECT_ENVIRONMENT=/tmp/agrojud-spec027-venv UV_CACHE_DIR=/tmp/agrojud-spec027-uv-cache npm run openapi:check` passaram. OpenAPI e tipos TypeScript estão sincronizados.
- Navegador: `E2E_API_PORT=18775 E2E_FRONTEND_PORT=18776 E2E_EVIDENCE_DIR=/tmp/agrojud-spec027-qa ./scripts/e2e.sh` — 26/26 cenários passaram no Chromium. Foram exercitados os temas, criação a partir do catálogo TPU, duplicação, edição/versionamento, arquivamento, buscas salvas, compatibilidade dos fluxos de coleta, grupos multitribunal, filtros e viewport móvel. Uma captura sintética do Radar foi salva fora do repositório em `/tmp/agrojud-spec027-qa/spec027-radar-busca-personalizada.png`.
- `graphify update .` atualizou o grafo do repositório.

| Critério | Resultado |
| --- | --- |
| [x] AC1: três temas oficiais sem IDs ou estratégia na decisão principal | Passou: catálogo, API e E2E |
| [x] AC2: compilação usa somente códigos aprovados e operadores definidos | Passou: catálogo/compilador e testes backend |
| [x] AC3: execução relacionada ao agro exige classe e assunto rural; classe isolada não confirma vínculo rural | Passou: critérios AND e testes de compilação |
| [x] AC4: recuperação judicial por classe, assunto ou ambos numa busca/query lógica; deduplicação e motivos preservados | Passou: testes backend de compilação, representação, explicação e CNJ |
| [x] AC5: criar do zero/duplicar com catálogo validado e sem query livre | Passou: API, Vitest e Playwright |
| [x] AC6: edição imutável, histórico de versões e arquivamento preservado | Passou: PostgreSQL isolado, API e Playwright |
| [x] AC7: compatibilidade com IDs/snapshots/jobs/buscas salvas legadas | Passou: fixtures e testes de compatibilidade sem reescrever histórico |
| [x] AC8: um ou vários tribunais preservam membros e checkpoints independentes | Passou: testes de grupos e Playwright |
| [x] AC9: API/OpenAPI, tipos TypeScript, frontend e Playwright | Passou: OpenAPI check, 72 Vitest e 26 Playwright |
| [x] AC10: migration e regressão de coleta; sem mudanças em retries, leases, paginação, checkpoints, rate limit ou deduplicação | Passou: suíte backend de 411 testes e E2E existentes |
| [x] AC11: documentação e diff revisados; probe externa não presumida | Passou: documentos e diff revistos; nenhuma probe DataJud foi executada |

A ativação de consultas reais continua limitada pelas evidências S1/S2/S5 vigentes e por tribunal. Nenhuma probe nem coleta real DataJud foi executada para esta unidade. O tema de execuções relacionadas ao agro permanece condicionado à evidência S5 da combinação vigente; testes locais, fixtures e Playwright não ampliam a cobertura externa.
