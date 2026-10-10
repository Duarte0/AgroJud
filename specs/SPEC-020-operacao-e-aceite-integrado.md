# SPEC-020 — Operação e aceite integrado

Status: DONE

Milestone/Spike: M9

## Dependências

[SPEC-018](SPEC-018-indicadores-da-base-local.md), [SPEC-019](SPEC-019-exportacao-csv.md)

Referências: [Plano](../IMPLEMENTATION_PLAN.md) e [PRD](../PRD.md). Executar somente esta unidade; não implementar suas sucessoras.

## Objetivo
Comprovar instalação, operação e recuperação do MVP completo, distinguindo demonstração sintética e integração real.

## Contexto
M9 é aceite integrado, não substitui testes acumulados nas etapas anteriores.

## Escopo
Compose completo, documentação final, backup/restauração, ensaio de operação e matriz de aceites.

## Requisitos técnicos e contratos
- Frontend servido por container com fallback de SPA e proxy /api para API; publicação em localhost. PostgreSQL interno. API e worker compartilham imagem backend.
- Migrações como passo explícito, volume persistente e encerramento gracioso; shutdown não exclui dados nem transforma job interrompido em sucesso.
- Separar projetos/volumes demo e real. Reset exige alvo demo explícito e recusa real. Não executar limpeza em banco existente para facilitar demonstração.
- Backup por ferramenta PostgreSQL, restauração em projeto descartável separado e comparação de processos, versões, triagem, acompanhamento e jobs.
- Backup pode conter job running: ambiente restaurado inicia com worker parado; revisar configuração da fonte e permitir recuperação por lease somente após verificação. Não disparar consulta real como efeito oculto do ensaio.
- README: instalação, configuração, comandos, portas, migrations, testes, geração de tipos, operação dos jobs, diagnóstico e limites conhecidos.
- Documentar arquitetura, dados, regras, cobertura, relação entre observação e evento, e limitações de uso descritas no PRD.
- Roteiro: coletar, interromper antes/depois de commit, recuperar, revisar, acompanhar, observar atualização e exportar.
- Medição controlada: máquina/configuração, fonte, volume, páginas, duração, retries, tamanho do banco e resultados. Não publicar alegações de throughput sem amostra.
- Matriz de aceite: local/sintético, transporte real, paginação real e catálogo real por capacidade. S1/S2 da SPEC-003 e itens necessários de SPEC-010 devem estar aprovados para aceite real.

## Comportamento esperado
Checkout limpo executa demonstração sem depender do CNJ. Ambiente real não substitui falha por fixtures. Restore comprova dados sem ativar operações externas inadvertidamente.

## Decisões importantes
Esta SPEC só fica DONE integralmente após aceite real requerido. Se fonte impedir validação, registrar entregas locais concluídas e status BLOCKED_VALIDATION; não usar “MVP validado” sem qualificar alcance.

## Critérios de aceitação
- AC1: instalação limpa e Compose completo funcionam com o README.
- AC2: backup/restore preserva entidades e decisões, sem alteração do ambiente de origem.
- AC3: jornada integrada sintética e testes de falha passam.
- AC4: evidências reais de S1/S2 e catálogo requerido estão vinculadas e aprovadas.
- AC5: logs, exportações e UI identificam fonte e não expõem credenciais.
- AC6: documentação descreve limites e medições reproduzíveis.

## Testes necessários
Suíte unitária/PostgreSQL serial, contratos OpenAPI, typecheck/build, Playwright, smoke de imagens, reinício de serviços, backup/restauração isolada e diagnóstico real limitado separado. Falha externa não é skip silencioso de AC4.

## Erros e edge cases
Migrations pendentes impedem readiness. Restore com versões incompatíveis falha explicitamente. Portas ocupadas têm override documentado. Impossibilidade de CNJ mantém aceites reais pendentes.

## Fora do escopo
Deploy público, autorização de uso comercial, escalabilidade distribuída, novos recursos e correções que ampliem silenciosamente o MVP.

## Evidência e conclusão

Validação local e aceite real concluídos em 10/10/2026. AC4 passou após a aprovação de S1/S2/S5 e aceites no stack `agrojud-real`; a matriz abaixo separa essas evidências reais da validação local/sintética.

### Matriz de aceite por capacidade

| Capacidade | Estado | Evidência e efeito |
| --- | --- | --- |
| Operação sintética local | VALIDATED | Compose descartável, migration explícita, interface Nginx, proxy `/api`, jobs, revisão, acompanhamento, atualização e CSV passaram; não valida a fonte externa. |
| Transporte real, autenticação e resposta do endpoint | VALIDATED (10/10/2026) | A [probe S1/S2](../docs/evidence/datajud-tjgo-validacao-2026-10-10.json) fez 6 requisições HTTP 200 (8,5–32,1 s) após o read timeout passar a 60 s configurável. A execução de 08/10 expirou em 20 s. |
| Filtros reais e busca exata por CNJ | VALIDATED (10/10/2026) | Filtro de intervalo no formato compacto `YYYYMMDDHHMMSS` (datas ISO não casavam documentos) e busca exata por CNJ observado validados na probe S1/S2. |
| Ordenação e paginação real em duas páginas | VALIDATED (10/10/2026) | Duas páginas não vazias com sort `@timestamp` + `id.keyword`, cursor avançando e sobreposição zero; a coleta passou a usar esse sort. A amostra não teve empates no valor primário. |
| Taxonomia oficial dos códigos TPU pesquisados e aplicabilidade ao TJGO | VALIDATED | Consulta ao SGT e detalhes de aplicabilidade constam em [`catalogo-tematico-tpu-2026-10-09.json`](../docs/evidence/catalogo-tematico-tpu-2026-10-09.json); valida apenas os códigos pesquisados. |
| Semântica dos filtros DataJud e exemplos estruturados reais para presets/sinais (S5) | VALIDATED, exceto `sinal.penhora` (10/10/2026) | A [probe S5](../docs/evidence/catalogo-tematico-datajud-2026-10-10.json), com critério aprovado pelo usuário (todos os hits contêm código pedido e ao menos 3 documentos distintos), validou os 4 presets e as regras de leilão (311) e recuperação judicial (12041). O movimento 11382 não aparece no índice TJGO; `sinal.penhora` segue desabilitado no real. |

| Critério | Estado | Evidência registrada |
| --- | --- | --- |
| AC1 | PASSOU localmente | `./scripts/e2e.sh` construiu e subiu PostgreSQL, API, worker e frontend Nginx em projeto E2E isolado, aplicou migration explicitamente, validou fallback SPA e proxy `/api`; 17 cenários Playwright passaram. O smoke adicional confirmou `agrojud-worker --check`, liveness/readiness da API e rotas frontend/proxy. `docker compose config --quiet` passou para demo, real, test e restore. |
| AC2 | PASSOU localmente | `backup-db.sh` gerou `pg_dump` custom e manifesto `0600`; SHA-256 e `pg_restore --list` passaram. Contagens e digests de 32 tabelas foram comparados antes/depois na origem e no restore descartável, sem mutação observada na origem. Foram preservados 3 processos, 16 representações, 16 versões, 2 triagens, 5 entradas de histórico de triagem, 3 entradas de acompanhamento, 6 eventos de acompanhamento, 18 jobs, 18 tentativas e checkpoints, 3 novidades e 1 sinal; revisão `20261009_0010`. O restore usou projeto `agrojud-restore-spec020`, PostgreSQL em `tmpfs`, sem API/frontend/worker, e foi removido ao final. |
| AC3 | PASSOU localmente | 280 testes backend passaram em PostgreSQL isolado (incluindo interrupção real por subprocesso antes/depois do commit em `test_real_subprocess_crash_before_and_after_commit_matches_continuous_run`); 55 testes frontend passaram; lint, typecheck, build e OpenAPI passaram; Playwright passou 17/17. A jornada integrada cobriu reinício da API com job na fila, coleta sintética, triagem, acompanhamento, atualização e CSV. |
| AC4 | PASSOU (10/10/2026) | S1/S2 e S5 aprovados nas evidências acima. Aceite real no stack `agrojud-real` (migration explícita, API, worker e frontend): descoberta `rural.credito_contratos` em ajuizamentos de 05/2026 com `hit_budget` 200 terminou `completed` com 183 hits confirmados, 183 válidos, 0 rejeitados e 3 requisições até página vazia; atualização por CNJ de um processo coletado e acompanhado terminou `completed` com estado `found`; a interface exibiu "Fonte DataJud habilitada". Nenhum CNJ ou ID foi registrado. |
| AC5 | PASSOU localmente | UI e job identificam `demo`/`synthetic`; exportação inclui origem. A chave DataJud fica na configuração do backend, não no build/container frontend. Testes verificam que mensagens/logs não expõem senha e que a falha real não aciona fixture. |
| AC6 | PASSOU localmente | Este runbook e o README cobrem instalação, configuração, portas, migrations, jobs, testes, tipos OpenAPI, diagnóstico, semântica dos dados e limites de uso. A medição sintética reproduzível está registrada no runbook e não faz alegação de throughput. |

Comandos executados: suíte backend serial em Compose com Ruff, formatação, mypy e pytest (`280 passed`, um aviso de depreciação Starlette); `npm run lint`, `npm run typecheck`, `npm test` (`55 passed`), `npm run build`, `npm run openapi:check`; `scripts/e2e.sh` (`17 passed`); smoke de imagens/serviços; `scripts/backup-db.sh demo ...`; `scripts/restore-backup.sh demo ...`. O escopo e as saídas locais são sintéticos; as falhas de fonte foram testadas por simulação e não substituem S1/S2/S5.

Revalidação em 10/10/2026 após habilitar o real: 298 testes backend, Ruff, formatação e mypy; `npm run lint`, `npm run typecheck`, `npm test` (`56 passed`), `npm run build`; schema OpenAPI exportado idêntico ao versionado; `scripts/e2e.sh` (`17 passed`) em demo isolado.

**Conclusão:** AC1 a AC6 concluídos; DONE em 10/10/2026. Limites: `sinal.penhora` não tem código observado no TJGO; janelas S5 diferentes por item servem só para obter exemplos; a latência real (até ~32 s por página) torna coletas grandes lentas. Entrega técnica não equivale a autorização de operação profissional ou comercial. Não marcar milestones anteriores concluídos sem suas evidências.

