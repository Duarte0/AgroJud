# SPEC-018 — Indicadores da base local

Status: DONE

Milestone/Spike: M9

## Dependências

[SPEC-017](SPEC-017-buscas-salvas-e-agendamento.md)

Referências: [Plano](../IMPLEMENTATION_PLAN.md) e [PRD](../PRD.md). Executar somente esta unidade; não implementar suas sucessoras.

## Objetivo
Apresentar indicadores descritivos corretos sobre a base persistida.

## Contexto
M9 adiciona visão geral após núcleo e acompanhamento. Dados representam a amostra local, não o TJGO inteiro.

## Escopo
Consultas agregadas, endpoint overview e página inicial com cards/distribuições simples.

## Requisitos técnicos e contratos
- GET /overview aceita o mesmo recorte processual da lista quando aplicável e retorna filtros resolvidos, generated_at e data_source.
- Métricas: processos distintos, representações, triagem pending/relevant/discarded, acompanhados ativos, novidades pendentes, sinais vigentes por categoria, temas e últimas coletas.
- Identificar unidade de cada métrica: processos, representações, ocorrências ou jobs. Temas sobrepostos não formam partição somável.
- Preservar pending implícito de processos sem registro de triagem.
- Usar DISTINCT/EXISTS ou pré-agregação para evitar multiplicação por assuntos, movimentos e sinais. Sinais históricos não inflam contagens vigentes.
- Leitura em transação read-only de snapshot consistente por resposta. Não criar tabelas de materialização/caches externos.
- Frontend usa TanStack Query e tipos gerados; root passa a overview. Link de card abre lista com filtro correspondente.
- Mostrar última observação local e limitações de cobertura; nunca representar consulta bem-sucedida como garantia de atualização do tribunal.
- Recharts somente se distribuição precisar de gráfico; tabela simples é suficiente. Sem metas de performance inventadas.

## Comportamento esperado
Base vazia apresenta zeros verdadeiros; banco com erro apresenta indisponibilidade, não zeros. Filtros visíveis acompanham os números.

## Decisões importantes
Nenhuma taxa de êxito, risco, duração jurídica ou total do contencioso estadual. Quantidade por tema pode somar mais que processos distintos, com explicação.

## Critérios de aceitação
- AC1: agregados batem com contagens esperadas de fixture multicapa/multiassunto.
- AC2: filtros dos cards correspondem à lista.
- AC3: valores históricos de regras não contam como vigentes.
- AC4: erro e vazio são distintos na UI.
- AC5: consultas têm plano inspecionado com volume controlado.

## Testes necessários
Fixture com um processo em várias relações; decisões implícitas; regra antiga/nova; snapshots durante atualização; teste API e navegador de drill-down. EXPLAIN em banco de teste sem criar índices indiscriminados.

## Erros e edge cases
Datas ausentes não viram “atualizado agora”. Métricas independentes não são apresentadas como percentual sem denominador definido.

## Fora do escopo
BI externo, dashboards extensos, séries históricas sem snapshots e benchmarking contra universo judicial.

## Evidência e conclusão

Implementação local concluída e validada com dados sintéticos. A tela descreve a amostra persistida localmente; não estima o contencioso do TJGO nem afirma atualização do tribunal. A fonte real e os gates S1/S2/S5 não foram alterados. Não houve migration, cache externo nem tabela materializada.

### AC1 — Valores de fixture

Fixture PostgreSQL com dois processos, três representações, quatro relações de tema, triagem explícita em um processo, duas ocorrências pendentes, um acompanhamento ativo e quatro linhas de sinal (duas vigentes de penhora, uma versão histórica e uma vigente de leilão).

| Métrica | Esperado | Obtido |
| --- | ---: | ---: |
| Processos distintos | 2 | 2 |
| Representações | 3 | 3 |
| Triagem pending / relevant / discarded | 1 / 0 / 1 | 1 / 0 / 1 |
| Acompanhados ativos | 1 processo | 1 processo |
| Novidades pendentes | 2 ocorrências | 2 ocorrências |
| Sinal leilão vigente | 1 processo | 1 processo |
| Sinal penhora vigente | 1 processo | 1 processo |
| Tema 4968 — Crédito rural | 2 processos | 2 processos |
| Tema 4969 — Penhora | 1 processo | 1 processo |

A soma por tema é três, maior que os dois processos da base, como esperado para temas sobrepostos. Duas representações do mesmo processo com sinal de penhora continuam contando um processo.

### AC2 — Recorte e drill-down

Com `subject_code=4968`, `subject_name_exact=Crédito rural` e `decision=pending`, a visão geral e `/api/v1/processes` retornaram um processo; o total de representações foi dois. O filtro de novidades pendentes coincidiu com `/api/v1/news` (duas ocorrências). O recorte preserva todos os temas dos processos selecionados, inclusive o tema 4969 de uma representação do processo. O filtro `signal_category=penhora` retornou um processo tanto na visão geral quanto na lista. O teste Playwright clicou no cartão de processos e conferiu o total da paginação; também abriu a lista por um tema, confirmou código/nome exatos e voltou à visão geral com o recorte resolvido visível.

### AC3 — Vigência dos sinais

A fixture contém `sinal.penhora` versão histórica `0.9.0` marcada publicada/atual no registro e duas evidências de `1.0.0`. A API excluiu `0.9.0` pela versão vigente do catálogo; as duas evidências `1.0.0` contaram um único processo. Leilão `1.0.0` contou o segundo processo.

### AC4 — Vazio e indisponibilidade

No PostgreSQL vazio, API e UI exibiram contagens zero e distribuições vazias. Com falha de banco, a API respondeu `503 database_unavailable`; a UI apresentou erro e não mostrou zeros como sucesso.

### AC5 — Planos com volume controlado

`EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` foi executado em PostgreSQL de testes com 102 processos, 103 representações, 104 relações de assunto, quatro linhas de sinal e uma coleta. A tabela registra uma execução informativa; os tempos variam por ambiente e não constituem meta de performance.

| Consulta | Nó raiz | Linhas no nó raiz | Tempo observado |
| --- | --- | ---: | ---: |
| Agregados principais | Aggregate | 1 | 0,309 ms |
| Sinais vigentes por categoria | Aggregate | 2 | 0,071 ms |
| Distribuição por tema | Sort | 2 | 0,154 ms |
| Últimas coletas | Limit | 1 | 0,015 ms |

O teste de snapshot inseriu um assunto em uma segunda transação depois da consulta de métricas: a resposta da visão geral não incluiu a inserção, enquanto uma leitura posterior a encontrou. O endpoint inicia uma transação `REPEATABLE READ READ ONLY`, e o teste confirmou ausência de comandos de escrita na conexão da requisição.

### Validações

- Backend: `266 passed` em PostgreSQL isolado; Ruff check, Ruff format e mypy aprovados.
- Frontend: `54 passed` em Vitest; ESLint, typecheck, build e `openapi:check` aprovados. O build separa a rota inicial em chunk próprio.
- Navegador: `14 passed` em Playwright na stack demo isolada com PostgreSQL em `tmpfs`; Browser MCP indisponível neste ambiente. A captura visual ficou em `/tmp/agrojud-spec018-evidence/spec018-visao-geral.png`.

Essas evidências concluem somente a SPEC-018 local. A SPEC-019, a SPEC-020 e a validação externa DataJud/TJGO continuam fora desta entrega.

