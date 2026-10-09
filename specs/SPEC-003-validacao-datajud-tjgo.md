# SPEC-003 — Validação DataJud/TJGO

Status: READY

Milestone/Spike: S1/S2

## Dependências

[SPEC-002](SPEC-002-contratos-e-adaptadores-de-fonte.md)

Referências: [Plano](../IMPLEMENTATION_PLAN.md) e [PRD](../PRD.md). Executar somente esta unidade; não implementar suas sucessoras.

## Objetivo
Produzir evidência limitada e reproduzível sobre o contrato TJGO e sua paginação.

## Contexto
S1/S2 investigam capacidades externas. Resultado inconclusivo é válido como diagnóstico, mas não aprova a integração real.

## Escopo
Comando de probe, testes dos limites, relatório sanitizado e matriz de capacidades. Sem carga no banco de produto.

## Requisitos técnicos e contratos
- Probe reutiliza transporte de chamada única da SPEC-002. Máximo seis requisições por execução, até 100 hits por página e sem retry automático.
- Primeiro validar envelope e consulta por recorte/numero público conhecido; obter um número somente de resultado público observado, sem inventá-lo.
- Testar sort documentado por @timestamp e candidato de desempate por identificador de origem keyword apenas se aceito. Registrar consulta exata sanitizada e sort recebido.
- Obter pelo menos duas páginas não vazias para o teste de paginação; repetir uma consulta e avaliar sobreposição, avanço e empates quando disponíveis.
- Relatório: horário, endpoint, orçamento, duração, código HTTP, shape dos campos, capacidades e justificativa. Não incluir token nem dados reais completos no Git.
- Capacidades separadas: envelope, campos essenciais, consulta por CNJ, filtros, identidade de origem, sort composto e paginação.
- Valores da matriz: VALIDATED, INCOMPATIBLE, INCONCLUSIVE, com evidência por item. Mudança de consulta/sort exige evidência nova.
- Para IDs, verificar relação entre _id, _source.id e numeroProcesso na amostra; não afirmar estabilidade histórica a partir de uma leitura.
- Falta de amostra com empates deve aparecer como limite da evidência, não como teste aprovado.

## Comportamento esperado
Sem chave, falhar localmente. Timeout encerra o probe com diagnóstico. Ferramenta não altera configuração de ativação automaticamente.

## Decisões importantes
A SPEC pode concluir ferramenta, testes e relatório com resultado INCONCLUSIVE. Nesse caso S1/S2 pertinentes ficam pendentes no índice. Somente capacidades VALIDATED podem liberar comportamento real, por atualização explícita e revisável da matriz.

## Critérios de aceitação
- AC1: limites de requisições, tamanho e sanitização são garantidos.
- AC2: cada capacidade tem resultado e evidência ou motivo de ausência.
- AC3: paginação só é validada quando duas páginas, cursor e ordenação candidata forem observados.
- AC4: indisponibilidade não produz registros=0 como sucesso nem libera habilitação.

## Testes necessários
AC1: transporte falso conta chamadas, timeouts e saída. AC2/AC4: relatório para 401, 429, timeout e envelope inválido. AC3: fixtures com empates, cursor repetido e ordenação rejeitada; complementar com execução remota limitada.

## Erros e edge cases
Recorte sem dados não prova paginação. Documento novo entre páginas não prova defeito local; registrar mutabilidade. Endpoint aceitar sort não comprova unicidade global. Não contornar rejeição usando endpoint restrito.

## Fora do escopo
Scraping, carga histórica, retries de produção, dedução de códigos TPU e promessa de snapshot consistente.

## Evidência e conclusão
Guardar relatório sem payload completo em documentação técnica. Separar “entrega do probe DONE” de “S1/S2 aprovados”. Bloqueio externo não é escondido por testes verdes.

