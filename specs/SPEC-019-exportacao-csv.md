# SPEC-019 — Exportação CSV

Status: BLOCKED_DEPENDENCY

Milestone/Spike: M9

## Dependências

[SPEC-017](SPEC-017-buscas-salvas-e-agendamento.md)

Referências: [Plano](../IMPLEMENTATION_PLAN.md) e [PRD](../PRD.md). Executar somente esta unidade; não implementar suas sucessoras.

## Objetivo
Exportar os resultados locais filtrados com procedência e integridade de conteúdo.

## Contexto
M9 reutiliza filtros da SPEC-011 e triagem da SPEC-013; não deve criar semântica paralela de busca.

## Escopo
Endpoint CSV, ação de download e testes de conteúdo/segurança de planilha.

## Requisitos técnicos e contratos
- GET /exports/processes.csv aceita o objeto de filtros da lista, excluindo page/page_size. Exporta todos os correspondentes ao recorte.
- Uma linha por processo; múltiplas classes/órgãos/assuntos em valores textuais ordenados e deduplicados, sem escolher uma capa arbitrariamente.
- Colunas estáveis: numero_cnj, tribunal, classes, graus, orgaos, assuntos, triagem, vinculo_rural, motivos_captura, acompanhado, primeira_observacao, ultima_observacao, data_source e exportado_em.
- UTF-8 com BOM, separador ponto e vírgula, aspas/linhas conforme regras CSV. Content-Type text/csv; filename sem input arbitrário e contendo demo/real.
- Neutralizar células textuais iniciadas, inclusive após whitespace, por =, +, -, @, tab ou CR com apóstrofo. Aplicar também às notas/motivos se futuramente incluídos.
- CNJ preservado como texto no arquivo sem fórmula do tipo ="..."; documentar que importação em planilha deve tratar a coluna como texto para evitar truncamento pelo aplicativo.
- Uma leitura consistente em transação read-only; buscar em lotes, escrever artefato temporário antes de enviar headers. Falha gera erro estruturado, não download de sucesso truncado.
- Limite de 50.000 processos por exportação síncrona, configurável; excedente retorna 422 EXPORT_LIMIT_EXCEEDED e pede restringir filtros, sem truncar silenciosamente.
- Apagar temporário após envio/cancelamento. Não persistir exportação nem criar serviço de arquivos no MVP.
- UI indica carregamento/erro e respeita filtros ativos; tipos OpenAPI atualizados.

## Comportamento esperado
Resultado vazio produz apenas cabeçalho. Arquivo informa demo/real em coluna e nome. Erro de banco não retorna CSV vazio bem-sucedido.

## Decisões importantes
Limite é orçamento operacional de exportação, diferente do limite de 2.000 hits por coleta. Não adicionar XLSX para contornar comportamento de planilha.

## Critérios de aceitação
- AC1: conjunto de CNJs corresponde à consulta filtrada completa.
- AC2: caracteres, quebras de linha e separadores permanecem parseáveis.
- AC3: conteúdo de fórmula é neutralizado e CNJ original pode ser recuperado.
- AC4: erro/limite não gera arquivo parcial apresentado como sucesso.
- AC5: artefatos temporários são removidos e origem está explícita.

## Testes necessários
Parse de ida/volta com biblioteca CSV, fixture multicapa e caracteres portugueses; payloads de fórmula/whitespace; zeros iniciais; limite exato/excedido; falha durante geração e cancelamento. Playwright de download com filtros.

## Erros e edge cases
Campo ausente vira célula vazia, não dado fabricado. Recorte não pode ser alterado no meio do snapshot. Cliente fechando conexão não deixa arquivo acumulado.

## Fora do escopo
XLSX, armazenamento permanente, envio por e-mail, exportar bruto e relatórios jurídicos.

## Evidência e conclusão
Exemplo sintético de arquivo, comparação de IDs exportados e testes de falha. Nenhum CSV real precisa ser versionado.

