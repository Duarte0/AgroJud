# SPEC-009 — Retries e recuperação

Status: READY

Milestone/Spike: M4/S4

## Dependências

[SPEC-008](SPEC-008-paginacao-e-checkpoints.md)

Referências: [Plano](../IMPLEMENTATION_PLAN.md) e [PRD](../PRD.md). Executar somente esta unidade; não implementar suas sucessoras.

## Objetivo
Recuperar falhas transitórias e interrupções sem perder progresso nem esconder falhas permanentes.

## Contexto
Fecha M4 e S4 sobre o coletor transacional da SPEC-008.

## Escopo
Política persistente de retries, retomada/continuação, revarredura identificada e harness de testes de interrupção.

## Requisitos técnicos e contratos
- Até cinco tentativas HTTP por página em cada ciclo, incluindo a inicial. Incrementar tentativa antes do HTTP, de modo que crash também consuma orçamento.
- Retry para timeout, falha transitória de conexão, 429 e 5xx. 400/401/403 e CONTRACT não recebem retry cego.
- Backoff com full jitter entre 0 e min(60s, 2s × 2^(tentativa-1)). Respeitar Retry-After válido como mínimo adicional ao horário mais cedo permitido; suportar segundos e HTTP-date.
- Espaçamento mínimo de 1s entre inícios de requisições; armazenar cooldown da fonte para que reinício e troca de job não burlem 429.
- Salvar next_attempt_at e liberar lease durante retry_wait; não dormir segurando reserva/transação. Relógio e aleatoriedade injetáveis em testes.
- Falha SQL aborta a transação; desconexão/deadlock transitório admite recuperação limitada com até cinco tentativas de persistência por página/ciclo. Violação de integridade não vira retry infinito.
- Em resposta de commit desconhecida, reconectar e verificar checkpoint/revisão antes de repetir ingestão.
- Comandos: resume(failed|cancelled) preserva cursor e cria novo ciclo de tentativas; continue(partial/limit) amplia orçamento; restart_scan cria nova coleta e job com predecessor.
- Resume com cursor sabidamente inválido retorna conflito e exige restart_scan explícito. Nenhuma reinicialização silenciosa.
- Se já existe execução equivalente ativa, retornar essa execução sem reativar outra. Concorrência protegida por restrição do banco.
- Recuperações por lease expirada sem progresso limitadas a cinco por ciclo; ao exceder, failed/recovery_exhausted. Progresso confirmado zera essa contagem.
- Falha em gravar estado por banco indisponível deixa a lease expirar; não anunciar failed persistido sem confirmação.

## Comportamento esperado
Reiniciar processo não zera retry/cooldown. Páginas confirmadas não são contadas novamente. Estado final preserva causa e dados parciais.

## Decisões importantes
Somente decisão manual inicia novo ciclo após esgotamento. Reprocessamento de quarentena da SPEC-006 é independente de retry de rede.

## Critérios de aceitação
- AC1: backoff e Retry-After sobrevivem ao reinício.
- AC2: erros permanentes não repetem chamadas.
- AC3: crashes antes/depois do commit equivalem à execução contínua.
- AC4: token vencido, cancelamento e resposta tardia não confirmam página.
- AC5: resume/continue/restart_scan preservam semânticas distintas e auditoria.

## Testes necessários
Testes determinísticos de relógio/jitter; 429 seguido de sucesso, 5xx esgotado e 401; reinício em retry_wait; commit desconhecido; kill real do subprocesso antes/depois do commit com PostgreSQL; dois consumidores com barreiras. S4 exige evidência do ensaio, não só mock do repositório.

## Erros e edge cases
Retry-After inválido usa backoff local; passado não gera atraso negativo; futuro longo é persistido sem espera bloqueante. Não garantir exactly-once remoto. Novas execuções não contornam cooldown da fonte.

## Fora do escopo
Scheduler, circuit breaker distribuído, multiworker operacional e reparação automática de dados inválidos.

## Evidência e conclusão
Comparar contagens, checksums e checkpoints da execução contínua/recuperada; registrar S4. Aceite de fonte real permanece dependente de SPEC-003.

