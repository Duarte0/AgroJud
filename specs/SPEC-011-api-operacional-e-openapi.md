# SPEC-011 — API operacional e OpenAPI

Status: BLOCKED_DEPENDENCY

Milestone/Spike: M5

## Dependências

[SPEC-009](SPEC-009-retries-e-recuperacao.md), [SPEC-010](SPEC-010-catalogo-tematico-versionado.md)

Referências: [Plano](../IMPLEMENTATION_PLAN.md) e [PRD](../PRD.md). Executar somente esta unidade; não implementar suas sucessoras.

## Objetivo
Expor o núcleo persistido por API tipada e estabilizar a integração do frontend.

## Contexto
M5 sucede prova de recuperação. Endpoint HTTP não executa a coleta em background do FastAPI.

## Escopo
API de jobs, processos, representações, movimentos, presets e ambiente; OpenAPI e geração de tipos.

## Requisitos técnicos e contratos
- Prefixo /api/v1. POST /jobs recebe kind discovery|refresh_number e critérios tipados; retorna 202 com job_id, status, reused e Location. Só confirma após enqueue no banco.
- GET /jobs e /jobs/{id}; POST /jobs/{id}/cancel, /resume, /continue e /restart-scan. Comandos enfileirados retornam 202; cancelamento retorna 200 com estado persistido ou intenção de cancelamento.
- GET /processes, /processes/{id}, /processes/{id}/representations e /processes/{id}/movements. Incluir data da fonte, coleta, representação, diagnóstico e identificação do ambiente.
- GET /presets e /environment; listar itens desabilitados com motivo e fonte demo/real, sem credenciais.
- Filtros locais: CNJ, assunto, classe, órgão, coleta e preset. CNJ de entrada admite pontuação conhecida, normaliza para 20 dígitos; UUID identifica recurso local.
- Listas: page>=1, page_size default 25 e máximo 100; resposta items/page/page_size/total. Processos ordenados por numero_cnj e id; jobs por created_at desc e id; movimentos por data com nulos ao final, depois id.
- Consultas com assunto/classe/órgão devem exigir correspondência na mesma representação quando combinadas, evitando misturar capas para fabricar match.
- Erro uniforme: error.code, message, details e request_id. 404 recurso ausente, 409 estado/conflito/feature indisponível, 422 entrada inválida, 503 banco indisponível.
- GET somente leitura. Não expor bruto completo por padrão. Serializar DTOs Pydantic dentro da vida útil da sessão, sem lazy loads posteriores.
- Gerar schema OpenAPI determinístico e tipos via openapi-typescript; CI compara geração sem chamadas ao CNJ. Dependências Node mínimas podem existir antes da aplicação React.

## Comportamento esperado
Duas criações equivalentes retornam o mesmo job ativo. Frontend recebe totais locais exatos, enquanto o total remoto do job conserva relation e ausência.

## Decisões importantes
API de coleta exige capacidades reais aprovadas, mas leitura local continua disponível durante indisponibilidade remota. Não reinterpretar erro da fonte como erro de saúde do banco.

## Critérios de aceitação
- AC1: criar/acompanhar/cancelar/retomar funcionam pelo contrato HTTP.
- AC2: GETs não alteram tabelas de domínio.
- AC3: filtros não duplicam processos nem cruzam capas incorretamente.
- AC4: OpenAPI gera tipos reproduzíveis e descreve erros/estados.
- AC5: ações incompatíveis retornam 409 sem efeito.

## Testes necessários
TestClient com PostgreSQL, disputa de criação, tabela de transições, 404/409/422/503, filtros multicapa e comparação de estado antes/depois dos GETs. Validar tipos gerados e schema em CI.

## Erros e edge cases
Página além do fim retorna items vazio. Ausência de movimentos mantém diagnóstico. Campos opcionais null não ganham valores de apresentação artificiais no backend.

## Fora do escopo
Triagem, watchlist, exportação, autenticação e API pública para terceiros.

## Evidência e conclusão
Sequência HTTP reproduzível criando coleta sintética e consultando resultado; logs sanitizados e comparação OpenAPI sem diff.

