# SPEC-010 — Catálogo temático versionado

Status: READY

Milestone/Spike: S5/M1

## Dependências

[SPEC-002](SPEC-002-contratos-e-adaptadores-de-fonte.md)

Referências: [Plano](../IMPLEMENTATION_PLAN.md) e [PRD](../PRD.md). Executar somente esta unidade; não implementar suas sucessoras.

## Objetivo
Definir presets e regras versionados com evidência independente da disponibilidade do transporte.

## Contexto
S5 separa taxonomia TPU, forma da query e observação no TJGO. O plano prevê descoberta rural e ampla de execuções/RJ.

## Escopo
Catálogo local versionado, compilação de filtros, pesquisa oficial dos códigos e matriz de habilitação. Sem engine de sinais nem coleta agendada.

## Requisitos técnicos e contratos
- Catálogo em JSON versionado: id estável, versão, nome, finalidade, códigos de classe/assunto/movimento, combinações, fonte oficial, data de consulta e evidência.
- Famílias: rural explícito (crédito/contratos rurais), execuções amplas, recuperação judicial ampla; sinais candidatos de penhora, leilão e RJ.
- Código e inclusão de descendentes exigem evidência TPU. Não ativar conjunto por busca textual de nomes ou copiar código de memória sem conferir.
- Evidências separadas: tpu_status, query_status e sample_status, cada uma validated/incompatible/inconclusive; habilitação real derivada das exigências aplicáveis.
- Regra sem exemplo estruturado correspondente permanece inativa no real. Sintético pode usar catálogo demonstrativo explícito, jamais rotulado como validado no TJGO.
- Lista dentro de um filtro é OR; dimensões diferentes são AND. Compilar query a partir de allowlist, sem DSL arbitrária.
- Intervalo de ajuizamento representado como início inclusivo e fim exclusivo; entrada de datas de usuário traduzida para o dia final completo em America/Sao_Paulo.
- Janela relativa default: 12 meses de calendário anteriores à execução; armazenar template separado do intervalo resolvido.
- Snapshot do preset/versionamento entra na coleta. Alteração de catálogo não modifica consultas em andamento.
- Casos amplos nunca confirmam vínculo rural. Retornar explicação dos códigos que causaram inclusão.

## Comportamento esperado
Uma consulta pode estar tecnicamente disponível e um preset permanecer desabilitado por falta de evidência temática. Motivo da desabilitação é consultável.

## Decisões importantes
Esta entrega pode ser concluída com catálogo parcialmente inconclusivo, desde que todas as famílias tenham investigação e resultado registrado. DONE da ferramenta/catálogo não promove S5 nem habilita itens pendentes.

## Critérios de aceitação
- AC1: todas as famílias têm códigos pesquisados ou motivo explícito de pendência.
- AC2: compilação OR/AND, intervalos e versões são determinísticos.
- AC3: item não validado não é oferecido como preset real habilitado.
- AC4: consulta salva conserva versão e justificativa.

## Testes necessários
Fixtures do catálogo, códigos desconhecidos, descendentes, limites de datas, query snapshots, mudanças de versão, ambientes demo/real e erro de validação. Pesquisa externa gera relatório separado dos testes locais.

## Erros e edge cases
Ausência de amostra não prova inexistência do tema. Código substituído não é atualizado silenciosamente. Não inventar disponibilidade real para tornar o catálogo “completo”.

## Fora do escopo
Regras de risco jurídico, probabilidades, editor visual de DSL, CRUD genérico de taxonomia e novos tribunais.

## Evidência e conclusão
Anexar links oficiais, versões e resultado por família. Capacidades remotas dependem das evidências correspondentes de SPEC-003, mesmo que a implementação deste catálogo esteja pronta.

