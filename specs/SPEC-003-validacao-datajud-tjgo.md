# SPEC-003 — Validação DataJud/TJGO

Status: DONE

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

### Entrega em 08/10/2026

- O comando `agrojud-datajud-probe` reutiliza o transporte HTTPX de tentativa única da SPEC-002. Executa no máximo seis requisições, com até 100 hits por página e sem retry.
- A consulta inicial usa recorte explícito de ajuizamento. Uma consulta exata por CNJ só é construída com um número observado nessa resposta. O sort documentado `@timestamp` e o candidato composto `@timestamp` + `id.keyword` são avaliados separadamente.
- O relatório registra consultas sanitizadas, status HTTP quando recebido, duração, shape allowlisted, comparações de repetição/paginação e estado/evidência/motivo de cada capacidade. Não grava resposta bruta, CNJ observado, identificadores de origem, valores de cursor ou credencial; não acessa o banco de produto.
- Sem chave, o comando grava diagnóstico local `INCONCLUSIVE`, envia zero requisições e encerra com código 2. Timeout encerra a execução sem retry e não é tratado como resultado vazio.

### Critérios de aceitação

- **AC1 — PASSOU:** testes com HTTPX MockTransport contam as chamadas, confirmam `size <= 100`, o teto de seis requisições, sanitização de CNJ/IDs/cursor/campos extras e ausência de retry em timeout.
- **AC2 — PASSOU:** o relatório fornece estado e evidência ou motivo de ausência para envelope, campos essenciais, CNJ, filtros, identidade, `@timestamp`, sort composto e paginação. Os cenários 401, 429, timeout e envelope inválido têm resultados distintos.
- **AC3 — PASSOU como regra de classificação local:** fixtures exercitam duas páginas não vazias, cursor, ordenação, empates, cursor repetido e sort composto rejeitado. Uma capacidade real de paginação só é marcada `VALIDATED` após duas páginas não vazias e avanço/ordenação observados. A execução remota desta entrega não atingiu esse critério.
- **AC4 — PASSOU:** teste local sem chave encerra sem HTTP; falha de rede produz `NETWORK`/`INCONCLUSIVE`, não sucesso vazio nem habilitação automática.

### Resultado remoto e limitações

Em 08/10/2026 (horário local; `2026-10-09T02:55:32Z`), foi executada uma probe ao endpoint TJGO com recorte de `2025-10-09` a `2026-10-09`, fim exclusivo. A primeira requisição expirou após 20.187 ms sem resposta HTTP. O probe encerrou após uma tentativa e o relatório versionado em [`docs/evidence/datajud-tjgo-validacao-2026-10-08.json`](../docs/evidence/datajud-tjgo-validacao-2026-10-08.json) mantém todas as capacidades externas como `INCONCLUSIVE`, com HTTP status ausente. Não se inferiu ausência de processos e não se repetiu a consulta.

**Validações locais:** Ruff check passou; 30 arquivos já estavam formatados; mypy passou em 21 arquivos de origem; pytest passou com **66 testes** em container Python 3.14.8 e PostgreSQL isolado. A porta padrão 55432 estava ocupada, então a execução isolada usou 55435. As configurações Compose demo/real/test passaram; a imagem de produção foi construída e o comando foi executado no container sem chave, confirmando falha local antes de HTTP.

**Conclusão da unidade:** DONE para ferramenta, testes e relatório. A evidência S1/S2 do endpoint real continua `INCONCLUSIVE`; esse estado não aprova contrato, identidade, filtro, sort ou paginação e não libera a fonte real. Obter nova amostra somente após motivo para nova verificação.

