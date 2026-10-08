# SPEC-020 — Operação e aceite integrado

Status: BLOCKED_DEPENDENCY

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
Tabela final de critérios, comandos, resultados e pendências. Não marcar milestones anteriores concluídos sem suas evidências. Entrega técnica não equivale a autorização de operação profissional.

