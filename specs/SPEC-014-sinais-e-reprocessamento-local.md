# SPEC-014 — Sinais e reprocessamento local

Status: DONE

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

- **10/10/2026:** com a evidência S5 da SPEC-010, `sinal.leilao` e `sinal.recuperacao_judicial` passaram a `enablement.real.enabled = true` (`state: validated`), sem mudar predicado nem versão. `sinal.penhora` segue desabilitado no real: o movimento 11382 não aparece no índice TJGO. Execução sem `rule_ids` no real continua retornando 409 enquanto houver regra inativa; selecione as regras habilitadas.

- O catálogo versionado registra `sinal.penhora` (11382), `sinal.leilao` (311) e `sinal.recuperacao_judicial` (12041). As três regras só estão habilitadas como `synthetic_only` em `demo`; todas permanecem desabilitadas em `real`, pois SPEC-010 não validou amostras estruturadas reais. Não se declarou capacidade real.
- As fixtures PostgreSQL exercitam correspondência positiva, negativa, código ausente e snapshot incompleto. O worker é executado com chamadas HTTPX bloqueadas pelo teste; a tentativa de HTTP falha o teste.
- A persistência usa `process_signals` com unicidade regra/versão/evidência/fingerprint, `signal_evaluations` append-only, snapshots imutáveis por entrada, checkpoint por UUID, lotes de até 100 e publicação atômica sob posse do lease.
- `test_signals.py` comprova cardinalidade idempotente, evidência consultável na timeline, preservação da triagem, sinal vigente marcado como defasado quando só existe versão completa anterior, interrupção após um lote, retomada no mesmo job e troca de versão com histórico preservado. Seleção vazia conclui com zero entradas; regra desconhecida e regra real inativa são rejeitadas. A API real não expõe sinais demo e não permite consultar ou retomar execução de outro ambiente.
- AC1–AC5 foram atendidos: explicação e proveniência são retornadas na API; repetição/retomada não duplica; publicação por processo espera todas as entradas; HTTP é bloqueado no worker; triagem e histórico humano permanecem intactos.
- Backend em PostgreSQL isolado: Ruff check, Ruff format check e mypy passaram; `pytest` passou com 227 testes. Inclui migrations em banco vazio e revisões anteriores.
- Frontend: lint, typecheck, 44 testes Vitest e build passaram. OpenAPI e tipos gerados foram comparados byte a byte com o contrato exportado da API atual.
- Playwright Chromium: 10 cenários passaram na pilha API/worker/PostgreSQL efêmera. O cenário da SPEC cria o sinal a partir de uma fixture sintética e segue a ocorrência exata até a timeline.

O reprocessamento usa exclusivamente snapshots locais e não altera o estado humano. A validação local conclui esta SPEC, mas não libera sinais reais, filtros reais ou integração DataJud; esses gates permanecem registrados em SPEC-003/SPEC-010.

