# SPEC-002 — Contratos e adaptadores de fonte

Status: READY

Milestone/Spike: M1

## Dependências

[SPEC-001](SPEC-001-fundacao-local.md)

Referências: [Plano](../IMPLEMENTATION_PLAN.md) e [PRD](../PRD.md). Executar somente esta unidade; não implementar suas sucessoras.

## Objetivo
Definir uma fronteira de fonte testável que nunca confunda falha com ausência de resultados.

## Contexto
M1 prepara ingestão sem banco de domínio. A documentação referenciada no PRD informa o formato esperado, mas a compatibilidade real depende da SPEC-003.

## Escopo
Contratos Python, cliente HTTP de chamada única e fonte sintética determinística. Catálogo completo fica na SPEC-010.

## Requisitos técnicos e contratos
- Operações `fetch_page(query, cursor, page_size)` e construção de consulta por número CNJ. Ambas retornam `SourcePage` ou erro tipado; nenhuma faz retry internamente.
- Consulta imutável: tribunal TJGO, filtros permitidos, intervalo de ajuizamento, versão do preset e sort. Cursor é lista opaca de valores retornados em sort.
- `SourcePage`: hits brutos, metadados de total/value/relation quando presentes, cursor final, indicação de lista vazia e horário de resposta.
- Separar validação do envelope (hits/lista/sort) de validação individual do payload. Um hit inválido permanece bruto para futura quarentena; envelope inválido gera CONTRACT.
- Hit preserva _id, _source e sort. Para normalizar capa, exigir _source objeto, numeroProcesso com 20 dígitos e tribunal TJGO. Identificador de origem deve ser texto não vazio e seguir a correspondência aprovada em SPEC-003; em fixtures usar _source.id igual a _id. Divergência real ainda não validada é rejeição, não fallback arbitrário entre os IDs.
- Erros: VALIDATION, AUTHENTICATION, AUTHORIZATION, RATE_LIMIT, NETWORK, SOURCE_UNAVAILABLE, CONTRACT; incluir status HTTP, retry_after e mensagem sanitizada quando disponíveis.
- HTTPX síncrono; POST somente ao endpoint público TJGO permitido. Credencial por configuração. Connect 5s, read/write 20s, pool 5s e redirects desativados.
- Preservar valores opcionais ausentes e campos extras no bruto. Não converter datas desconhecidas em data atual ou zero.
- Fonte sintética suporta conjunto fixo de páginas, cursores opacos, consulta vazia, mutação entre execuções e erros injetáveis.
- Isolamento da configuração impede utilizar fonte real em ambiente demo por fallback; a escolha é explícita.

## Comportamento esperado
Uma chamada corresponde a uma tentativa HTTP. Resposta 200 com hits vazio é sucesso vazio; HTML com 200 é CONTRACT; 429 carrega Retry-After para o worker futuro.

## Decisões importantes
Cliente real pode ser testado por transporte simulado antes da validação externa. Isso não habilita coleta real de produto. IDs e desempate remoto permanecem candidatos até a SPEC-003.

## Critérios de aceitação
- AC1: contratos distinguem página, vazio e cada categoria de erro.
- AC2: cliente preserva cursor e bruto, sem retry escondido.
- AC3: fixtures reproduzem páginas e falhas de maneira determinística.
- AC4: credenciais e corpo bruto não vazam em logs de erro.

## Testes necessários
AC1/AC2: HTTPX MockTransport para 200 válido/vazio, 400, 401, 403, 429, 5xx, timeout, JSON inválido e sort ausente em página paginada. AC3: repetição de fixtures e consulta por número. AC4: captura de logs.

## Erros e edge cases
Total com relation=gte é limite inferior, não denominador exato. Resposta sem movimentos preserva ausência, distinta de lista vazia. Consulta inválida falha antes de HTTP. Retorno de outro tribunal não será aceito silenciosamente na normalização.

## Fora do escopo
Retries, tabela de jobs, persistência, prova de compatibilidade TJGO e deduplicação de movimentos.

## Evidência e conclusão
Documentar contratos e cobertura simulada. DONE habilita desenvolvimento local; evidência real é controlada na SPEC-003.

