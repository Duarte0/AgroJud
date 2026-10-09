# S3 — Identidade técnica e reconciliação de ocorrências

## Decisão

Uma ocorrência é identificada por uma referência opaca à representação, SHA-256 do movimento normalizado e ordinal de multiplicidade entre conteúdos iguais. A referência representa uma capa/fonte específica; CNJ, grau e órgão não são combinados nem usados para deduplicar capas.

Os rótulos `FIRST_OBSERVED`, `KNOWN`, `NOT_PRESENT_IN_SNAPSHOT` e `ALTERATION_OBSERVED` descrevem somente observações técnicas. Nenhum deles declara existência, novidade, correção ou ausência de ato jurídico.

## Canonicalização

- JSON é codificado em UTF-8, com chaves de objeto ordenadas, separadores compactos e números finitos. A codificação mantém tipos, `null`, campos ausentes e ordem de arrays. O contrato é local ao projeto e não reivindica conformidade com RFC 8785.
- O SHA-256 do payload bruto usa essa codificação; reordenar qualquer array pode criar outra versão bruta.
- A reconciliação de movimentos não usa a posição do array. Complementos são ordenados por sua representação JSON canônica, mantendo repetições.
- Movimento normalizado preserva código, nome, órgão, complementos, campos extras, valor original de data/hora e valor interpretado quando possível.
- Data/hora com fuso é normalizada para UTC. Data/hora sem fuso recebe `timezone_ambiguous`; mantém o original como valor de comparação e não recebe fuso presumido. Valor não interpretável também mantém o original e não recebe valor normalizado.
- `movement-normalizer-v1` é guardado junto da representação normalizada. Comparar versões diferentes falha explicitamente e exige reprocessamento solicitado; não existe recálculo silencioso.

## Reconciliação

- Chave exata: `representation + content_sha256 + ordinal`, com ordinal iniciando em 1 para cada hash no snapshot.
- Multiconjuntos definem a contagem. A ordenação de movimentos não muda identidades; uma cópia adicional recebe novo ordinal. Histórico é cumulativo e não remove identidade quando ela some de um snapshot.
- Uma identidade exata já guardada é `KNOWN`; uma identidade que nunca foi observada é `FIRST_OBSERVED`; identidade histórica ausente do snapshot é `NOT_PRESENT_IN_SNAPSHOT`. Reaparecimento reutiliza a identidade histórica.
- A chave auxiliar contém código, data/hora, órgão e complementos estruturados, sem o nome descritivo nem campos extras. Data/hora com fuso usa UTC normalizado; sem fuso ou não interpretável usa o valor original. Código e data/hora ausentes ou nulos impedem a chave auxiliar, mas não impedem hash, identidade técnica ou retenção do conteúdo.
- Conteúdo diferente com chave auxiliar igual produz `ALTERATION_OBSERVED` e referências explícitas aos conteúdos anteriores e atuais. Uma única correspondência de cada lado pode ser indicada sem ambiguidade; grupos com mais de uma possibilidade ficam marcados como ambíguos e nunca são fundidos.
- Conteúdo distinto que colida no mesmo SHA-256 causa erro de integridade antes de qualquer deduplicação. SPEC-006 ainda deverá impor essa proteção na persistência.

## Fixtures sintéticas e resultados esperados

As fixtures ficam em `backend/tests/fixtures/occurrence_reconciliation.json`. Os nomes de representação e valores de movimento são fictícios e não identificam processos ou eventos reais.

| Cenário | Resultado esperado |
| --- | --- |
| Reordenar dois movimentos e os complementos | Mesmo conjunto de identidades; ocorrências atuais `KNOWN`; hash bruto do array reordenado pode mudar. |
| Multiplicidades `1 → 2 → 1 → 2` | Ordinais `1` e `2` preservados; ordinal ausente continua no histórico; reaparecimento é `KNOWN`. |
| Alterar nome ou campo extra mantendo a chave auxiliar | Nova versão `ALTERATION_OBSERVED`, versão anterior retida e referenciada; voltar ao conteúdo original é `KNOWN`. |
| Alterar complemento estruturado | Chave auxiliar diferente; conteúdo fica `FIRST_OBSERVED`, sem pareamento automático. |
| Mais de uma versão histórica com a mesma chave auxiliar | Evento de alteração com todas as referências candidatas e `ambiguous=true`. |
| Mesmo processo sintético em dois graus e dois órgãos | Quatro referências de representação resultam em quatro identidades distintas. |
| Data/hora sem fuso | Valor original é usado na chave; normalização não acrescenta `Z` nem offset. |
| Código/data-hora ausentes ou nulos | Hash do conteúdo continua disponível; não há chave auxiliar. |

## Limites

Este contrato é puro e local: não acessa fonte remota, não persiste histórico, não cria tabela, não faz fuzzy matching e não associa capas pelo número CNJ. Fixtures provam somente o comportamento determinístico deste algoritmo; não validam a estabilidade dos dados ou identificadores do DataJud.
