# SPEC-014 — Sinais e reprocessamento local

Status: BLOCKED_DEPENDENCY

Milestone/Spike: M7

## Dependências

[SPEC-013](SPEC-013-triagem-e-historico-humano.md)

Referências: [Plano](../IMPLEMENTATION_PLAN.md) e [PRD](../PRD.md). Executar somente esta unidade; não implementar suas sucessoras.

## Objetivo
Gerar sinais auditáveis por regras estruturadas e reprocessá-los localmente.

## Contexto
M7 utiliza o catálogo da SPEC-010 e ocorrências da SPEC-006, preservando triagem da SPEC-013.

## Escopo
Engine determinística, persistência versionada, job local, API e apresentação das evidências.

## Requisitos técnicos e contratos
- Regra possui id/versão, categoria, predicado estruturado e evidência de habilitação por ambiente. Sem texto livre interpretado, LLM ou regex de nomes como prova jurídica.
- Sinal identifica processo, representação, ocorrência/atributo, regra/versão, explicação e horário. Unique(regra, versão, evidência, fingerprint do resultado).
- Engine recebe dados locais normalizados e retorna resultados; nenhuma chamada HTTP. Dados insuficientes resultam diagnóstico de não avaliação, não sinal negativo conclusivo.
- Tipo de job reprocess_rules usa snapshots do conjunto de regras e dos IDs/versões de entrada no enqueue, evitando universo móvel durante execução.
- Entradas são as últimas versões completamente normalizadas de cada representação selecionada no enqueue. Se a representação mais recente estiver incompleta, manter resultado anterior com indicação de evidência defasada; não publicar sua ausência como desaparecimento de sinal.
- Cursor local por ID de entrada, lotes de 100, commit dos sinais/progresso com a mesma verificação de posse. Pode reutilizar mecanismo de jobs, não cursor DataJud.
- Versão de regras nova cria execução nova. Resultado vigente por processo só muda quando todas as entradas desse processo forem processadas; falha mantém versão anterior visível.
- Sinais anteriores permanecem históricos; trocar versão não apaga decisões humanas ou revisões de novidades.
- POST /rule-runs com seleção de processos ou filtro local resolvido retorna 202; GET /processes/{id}/signals informa vigente/histórico e proveniência.
- UI no detalhe exibe categoria, regra/versão e evidência clicável na timeline; comando de reprocessamento limitado à seleção explícita.

## Comportamento esperado
Reexecutar mesma regra/entrada mantém a mesma cardinalidade. Regra real não validada não roda como regra validada. Falha a meio da execução é visível e retomável.

## Decisões importantes
Sinal não confirma vínculo rural nem altera decisão humana. Penhora/leilão são categorias de eventos observados, sem cálculo de urgência.

## Critérios de aceitação
- AC1: todos os sinais são explicáveis e ligados a dados locais.
- AC2: repetição e retomada não duplicam sinais.
- AC3: nova versão conserva histórico e publicação consistente por processo.
- AC4: testes bloqueiam qualquer tentativa HTTP no reprocessamento.
- AC5: notas, triagem e novidades permanecem inalteradas.

## Testes necessários
Tabela de regras positivas/negativas/incompletas; PostgreSQL para unique e checkpoint; crash no meio de processo; troca de versão e reprocessamento repetido; Playwright de evidência. Teste explícito de ausência de chamada remota.

## Erros e edge cases
Regra inexistente/inativa: 409 ou 422 conforme seleção. Seleção vazia produz execução concluída com zero entradas explícitas. Evidência histórica não é eliminada ao desaparecer na fonte.

## Fora do escopo
Editor genérico de regras, classificação probabilística, análise documental e notificações.

## Evidência e conclusão
Registrar fixture, regra e sinal esperado; demonstrar reprocessamento com rede indisponível e preservação de decisões humanas.

