# SPEC-018 — Indicadores da base local

Status: BLOCKED_DEPENDENCY

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
Tabela de valores esperados/obtidos, consultas medidas e tela com recorte visível.

