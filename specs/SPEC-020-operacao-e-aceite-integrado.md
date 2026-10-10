# SPEC-020 — Operação e aceite integrado

Status: BLOCKED_VALIDATION

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

Validação local concluída em 10/10/2026. O aceite real permanece condicionado às evidências externas requeridas; nenhuma consulta real foi executada nesta unidade.

### Matriz de aceite por capacidade

| Capacidade | Estado | Evidência e efeito |
| --- | --- | --- |
| Operação sintética local | VALIDATED | Compose descartável, migration explícita, interface Nginx, proxy `/api`, jobs, revisão, acompanhamento, atualização e CSV passaram; não valida a fonte externa. |
| Transporte real, autenticação e resposta do endpoint | INCONCLUSIVE | A probe S1 expirou sem status HTTP após 20.187 ms. A [amostra manual posterior](../docs/evidence/datajud-tjgo-amostra-manual-2026-10-09.json) confirma somente envelope/campos essenciais e igualdade `_id`/`_source.id` para um hit, sem aprovar o transporte completo. |
| Filtros reais e busca exata por CNJ | INCONCLUSIVE | Sem evidência de consulta DataJud/TJGO que valide filtros ou busca exata; criação de jobs reais permanece bloqueada. |
| Ordenação e paginação real em duas páginas | INCONCLUSIVE | Não foi observado segundo cursor/página, desempate estável ou ausência de lacunas; nenhuma alegação de paginação real é aprovada. |
| Taxonomia oficial dos códigos TPU pesquisados e aplicabilidade ao TJGO | VALIDATED | Consulta ao SGT e detalhes de aplicabilidade constam em [`catalogo-tematico-tpu-2026-10-09.json`](../docs/evidence/catalogo-tematico-tpu-2026-10-09.json); valida apenas os códigos pesquisados. |
| Semântica dos filtros DataJud e exemplos estruturados reais para presets/sinais (S5) | INCONCLUSIVE | Não há amostras reais que provem os filtros nem payloads de movimentos correspondentes. Presets reais e regras de sinais seguem desabilitados. |

| Critério | Estado | Evidência registrada |
| --- | --- | --- |
| AC1 | PASSOU localmente | `./scripts/e2e.sh` construiu e subiu PostgreSQL, API, worker e frontend Nginx em projeto E2E isolado, aplicou migration explicitamente, validou fallback SPA e proxy `/api`; 17 cenários Playwright passaram. O smoke adicional confirmou `agrojud-worker --check`, liveness/readiness da API e rotas frontend/proxy. `docker compose config --quiet` passou para demo, real, test e restore. |
| AC2 | PASSOU localmente | `backup-db.sh` gerou `pg_dump` custom e manifesto `0600`; SHA-256 e `pg_restore --list` passaram. Contagens e digests de 32 tabelas foram comparados antes/depois na origem e no restore descartável, sem mutação observada na origem. Foram preservados 3 processos, 16 representações, 16 versões, 2 triagens, 5 entradas de histórico de triagem, 3 entradas de acompanhamento, 6 eventos de acompanhamento, 18 jobs, 18 tentativas e checkpoints, 3 novidades e 1 sinal; revisão `20261009_0010`. O restore usou projeto `agrojud-restore-spec020`, PostgreSQL em `tmpfs`, sem API/frontend/worker, e foi removido ao final. |
| AC3 | PASSOU localmente | 280 testes backend passaram em PostgreSQL isolado (incluindo interrupção real por subprocesso antes/depois do commit em `test_real_subprocess_crash_before_and_after_commit_matches_continuous_run`); 55 testes frontend passaram; lint, typecheck, build e OpenAPI passaram; Playwright passou 17/17. A jornada integrada cobriu reinício da API com job na fila, coleta sintética, triagem, acompanhamento, atualização e CSV. |
| AC4 | PENDENTE externo | A matriz por capacidade acima mostra S1/S2 e a parte DataJud de S5 como INCONCLUSIVE. A taxonomia TPU pesquisada tem validação oficial, mas isso não aprova filtros, respostas ou exemplos reais necessários ao aceite. |
| AC5 | PASSOU localmente | UI e job identificam `demo`/`synthetic`; exportação inclui origem. A chave DataJud fica na configuração do backend, não no build/container frontend. Testes verificam que mensagens/logs não expõem senha e que a falha real não aciona fixture. |
| AC6 | PASSOU localmente | Este runbook e o README cobrem instalação, configuração, portas, migrations, jobs, testes, tipos OpenAPI, diagnóstico, semântica dos dados e limites de uso. A medição sintética reproduzível está registrada no runbook e não faz alegação de throughput. |

Comandos executados: suíte backend serial em Compose com Ruff, formatação, mypy e pytest (`280 passed`, um aviso de depreciação Starlette); `npm run lint`, `npm run typecheck`, `npm test` (`55 passed`), `npm run build`, `npm run openapi:check`; `scripts/e2e.sh` (`17 passed`); smoke de imagens/serviços; `scripts/backup-db.sh demo ...`; `scripts/restore-backup.sh demo ...`. O escopo e as saídas locais são sintéticos; as falhas de fonte foram testadas por simulação e não substituem S1/S2/S5.

**Conclusão:** entregas locais e AC1, AC2, AC3, AC5 e AC6 concluídos. A SPEC permanece `BLOCKED_VALIDATION` pelo AC4 e só poderá passar a `DONE` após aprovação das evidências reais requeridas. Entrega técnica não equivale a autorização de operação profissional ou comercial. Não marcar milestones anteriores concluídos sem suas evidências.

