# SPEC-004 — Identidade e reconciliação

Status: BLOCKED_DEPENDENCY

Milestone/Spike: S3

## Dependências

[SPEC-002](SPEC-002-contratos-e-adaptadores-de-fonte.md)

Referências: [Plano](../IMPLEMENTATION_PLAN.md) e [PRD](../PRD.md). Executar somente esta unidade; não implementar suas sucessoras.

## Objetivo
Fixar e testar a identidade técnica de ocorrências, sem inventar identidade jurídica.

## Contexto
S3 resolve um risco anterior à persistência de movimentos. Não existe identificador global comprovado de movimentação no projeto.

## Escopo
Funções puras de canonicalização/comparação, fixtures sintéticas e documento de decisão. Sem banco e sem fonte remota.

## Requisitos técnicos e contratos
- Hash SHA-256 de JSON canônico com chaves ordenadas, codificação UTF-8 e sem dependência de espaços. Preservar tipos, ausente versus null e valores originais no bruto.
- Hash bruto de payload preserva ordem dos arrays: reordenação pode gerar versão de bruto distinta, mas nunca novas ocorrências por si só.
- Normalização de movimento: código, data/hora original e normalizada quando interpretável, nome, órgão, complementos e campos extras. Ordenar complementos para comparação, preservando sua multiplicidade.
- Identidade exata: representação + hash do movimento normalizado + ordinal 1..N entre conteúdos idênticos. Não usar posição no array original.
- Comparação usa multiconjuntos. Manter histórico de ocorrências já observadas; sumiço não exclui ocorrência e reaparecimento exato não é novo.
- Chave auxiliar de comparação: código + data/hora + órgão + complementos estruturados, sem nome descritivo. Serve para identificar alteração potencial, nunca para mesclar registros.
- Se o conteúdo muda mantendo chave auxiliar, emitir ALTERATION_OBSERVED com referência aos conteúdos envolvidos. Múltiplas correspondências permanecem ambíguas.
- Conteúdo sem correspondência prévia é FIRST_OBSERVED; conteúdo exato anterior é KNOWN; ausência atual é NOT_PRESENT_IN_SNAPSHOT. Nenhum rótulo significa novo ato jurídico.
- Data sem fuso não ganha fuso presumido; armazenar ambiguidade e usar valor original na comparação.

## Comportamento esperado
Reordenar movimentos/complementos não gera novidades. Duplicatas legítimas preservam contagem. Alteração de nome não apaga versão anterior nem produz conclusão de novo ato.

## Decisões importantes
Versão do algoritmo faz parte dos metadados da normalização. Mudança futura exige reprocessamento explícito; nunca recalcular identidades silenciosamente.

## Critérios de aceitação
- AC1: canonicalização é determinística e diferencia null, ausente, número e texto.
- AC2: multiplicidade é preservada sem depender da ordem.
- AC3: reaparecimento exato é KNOWN; alteração textual é alteração observada.
- AC4: representações diferentes nunca têm suas ocorrências fundidas.

## Testes necessários
AC1: tabela de casos e estabilidade entre execuções. AC2: permutações e duplicatas 1→2→1→2. AC3: nome alterado, campo extra, complemento alterado e pares ambíguos. AC4: dois graus e dois órgãos do mesmo CNJ.

## Erros e edge cases
Código/data ausentes impossibilitam classificação jurídica, mas bruto continua preservável. Colisão de hash com conteúdo divergente deve ser detectada na persistência e tratada como integridade, não deduplicação.

## Fora do escopo
Fuzzy matching, deduplicação entre graus, interpretação jurídica, sinais, baseline e banco.

## Evidência e conclusão
Entregar tabela entrada/resultado e testes da política. DONE fixa o contrato utilizado por SPEC-006 e SPEC-016; não depende de amostra real para comprovar as propriedades locais.

