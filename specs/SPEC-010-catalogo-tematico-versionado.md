# SPEC-010 — Catálogo temático versionado

Status: DONE

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

## Implementação entregue
- Catálogo JSON `backend/src/agrojud/sources/thematic_catalog.v1.json`, versão `1.0.0`, com presets versionados para crédito rural, execuções amplas e recuperação judicial por classe ou assunto, além dos três sinais candidatos.
- Evidência TPU por código, com situação atual, hierarquia direta, aplicabilidade TJGO e dimensão. Não há expansão de descendentes. Foram excluídos códigos inativos ou sem aplicação estadual.
- Pesquisa oficial registrada separadamente em [`docs/evidence/catalogo-tematico-tpu-2026-10-09.json`](../docs/evidence/catalogo-tematico-tpu-2026-10-09.json). O WebService público do SGT retornou a versão `06/10/2026`; links de referência: [WebService público do SGT](https://www.cnj.jus.br/sgt/infWebService.php), [portal TPU do CNJ](https://www.cnj.jus.br/programas-e-acoes/tabela-processuais-unificadas/) e [tutorial da API Pública DataJud](https://www.cnj.jus.br/wp-content/uploads/2023/05/tutorial-api-publica-datajud-beta.pdf).
- `backend/src/agrojud/sources/catalog.py` valida o catálogo, consulta disponibilidade por ambiente e compila somente dimensões allowlisted. Códigos dentro de uma dimensão viram OR; dimensões distintas viram AND. O compilador também devolve nome/código de cada filtro para explicar a inclusão.
- Datas finais escolhidas pelo usuário são inclusivas na entrada e viram fim exclusivo no dia seguinte. A janela default usa os 12 meses de calendário anteriores à data local de execução em `America/Sao_Paulo`, com template e intervalo resolvido armazenados separadamente.
- O snapshot da consulta salva versão do catálogo e do preset, justificativa, evidências, códigos efetivos, explicações, estado do vínculo rural e intervalo resolvido junto ao job. Retomadas revalidam esse snapshot sem recompilar contra o catálogo atual.
- Presets demonstrativos são rotulados como sintéticos. Todos os presets reais permanecem desabilitados porque `query_status` e `sample_status` seguem INCONCLUSIVE na SPEC-003. Os sinais permanecem candidatos, sem engine, e inativos no real por falta de amostra estruturada.

**Validação local em container Python 3.14.8 e PostgreSQL 18.6 isolado:** Ruff check passou; Ruff format check passou com 59 arquivos formatados; mypy passou em 41 arquivos; pytest passou com **191 testes**. Os 17 testes unitários da SPEC cobrem catálogo, código desconhecido, descendentes, gates demo/real, datas, payload OR/AND e snapshot após alteração simulada do catálogo. Um teste PostgreSQL confirma o snapshot após enqueue.

**Validação de build:** imagem de produção construída e carregou o JSON do catálogo (`1.0.0`, TPU `06/10/2026`); wheel Python construído e conferido com `thematic_catalog.v1.json` incluído. Compose de teste validado. Não houve migration nem chamada ao DataJud.

## Critérios de aceitação
- [x] AC1: todas as seis famílias previstas foram pesquisadas no SGT; códigos inativos/inaplicáveis foram excluídos com motivo.
- [x] AC2: OR/AND, datas inclusivas/semiabertas, janela de 12 meses, versões e explicações têm compilação determinística.
- [x] AC3: presets reais não ficam habilitados sem evidência requerida; demo informa que é demonstrativa e não valida o TJGO.
- [x] AC4: teste PostgreSQL confirma que o job salvo mantém versão, justificativa, códigos, intervalo e vínculo rural não confirmado.

## Testes necessários
Fixtures do catálogo, códigos desconhecidos, descendentes, limites de datas, query snapshots, mudanças de versão, ambientes demo/real e erro de validação. Pesquisa externa gera relatório separado dos testes locais.

## Erros e edge cases
Ausência de amostra não prova inexistência do tema. Código substituído não é atualizado silenciosamente. Não inventar disponibilidade real para tornar o catálogo “completo”.

## Fora do escopo
Regras de risco jurídico, probabilidades, editor visual de DSL, CRUD genérico de taxonomia e novos tribunais.

## Evidência e conclusão
Conclusão local: DONE em 09/10/2026 após os critérios e testes acima passarem. A investigação TPU consta no relatório externo por família. A validade taxonômica dos códigos está VALIDATED para os códigos incluídos; filtros DataJud e exemplos estruturados do TJGO permanecem INCONCLUSIVE na SPEC-003. Nenhum preset ou sinal foi habilitado no real, e S5 continua pendente. A conclusão desta ferramenta não promove o milestone M1 nem valida a fonte real.

