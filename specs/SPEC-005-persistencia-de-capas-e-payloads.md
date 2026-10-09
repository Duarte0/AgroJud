# SPEC-005 — Persistência de capas e payloads

Status: READY

Milestone/Spike: M2

## Dependências

[SPEC-002](SPEC-002-contratos-e-adaptadores-de-fonte.md)

Referências: [Plano](../IMPLEMENTATION_PLAN.md) e [PRD](../PRD.md). Executar somente esta unidade; não implementar suas sucessoras.

## Objetivo
Persistir uma página de capas e payloads com rastreabilidade e efeitos idempotentes.

## Contexto
M2 ocorre antes dos jobs. A identificação de coleta precisa existir sem depender da fila futura.

## Escopo
Migrations, modelos e repositórios de processo, representação, versão, coleta e observação; serviço de ingestão sem HTTP.

## Requisitos técnicos e contratos
- UUIDs locais; processo tem numero_cnj textual de 20 dígitos único. Não reconstruir número a partir de identificador composto.
- Representação única por fonte + tribunal + identificador de origem. Se a mesma chave passar a apontar para CNJ diferente, rejeitar com erro de integridade; não reassociar silenciosamente.
- Versão: representação, hash do JSON canônico, bruto JSONB, versão do normalizador, datas da fonte e primeira observação; unique(representação, hash).
- Canonicalização bruta: SHA-256 de JSON UTF-8 com chaves ordenadas e sem espaços insignificantes, preservando tipos, null e ordem dos arrays. Reordenação pode criar outra versão bruta; identidade de movimentos é responsabilidade separada de SPEC-004/006.
- Coleta: UUID, modo demo/real, critérios resolvidos JSONB, hash dos critérios, data de criação e coleta anterior opcional. Não possui estados de fila.
- Observação: coleta, chave da página, ordinal do hit, versão recebida e horário. Unique(coleta, chave_página, ordinal); replay com conteúdo diferente na mesma posição é conflito, não sobrescrita.
- Resultado por coleta/representação distinto das observações: permite explicar inclusão sem inflar quantidade de processos.
- Serviço recebe sessão/transação externa e não faz commit. Retorna contadores novos/atualizados/inalterados e identificação das versões.
- Campos estruturados: tribunal, classe, grau, órgão, ajuizamento e assuntos. Assuntos em relação própria para filtros, sem tornar o JSONB a única interface de consulta.
- Preservar datas originais e ambiguidade; valores parseáveis com fuso em timestamptz. Ausência não vira zero.
- Representação aponta à última versão observada localmente; expor separadamente eventual regressão da data informada pela fonte.
- Índices para CNJ, origem, assuntos, classe e órgão. Testes e banco operacional não compartilham schema.
- Validação real de identidade permanece condicionada à SPEC-003; fixtures possuem identidade explícita.

## Comportamento esperado
Uma nova coleta pode observar uma versão já existente sem copiá-la. Repetir a mesma página na mesma coleta não duplica observações ou resultados.

## Decisões importantes
Não criar job fictício para M2. A SPEC-007 adicionará vínculo entre job e coleta existente. Erro de hit nesta unidade é retornado ao chamador; armazenamento de quarentena pertence à SPEC-006.

## Critérios de aceitação
- AC1: mesma página repetida mantém contagens e associações.
- AC2: nova coleta do mesmo conteúdo acrescenta observação, não versão.
- AC3: capas distintas do mesmo CNJ são preservadas.
- AC4: rollback externo remove todos os efeitos da página.
- AC5: migrations aplicam em banco vazio e vindo de SPEC-001.

## Testes necessários
PostgreSQL real para AC1–AC5; casos de conflito de origem/CNJ, hash igual com conteúdo incompatível e regressão temporal. Usar fixtures, sem HTTP remoto.

## Erros e edge cases
Hit sem identidade não cria processo parcial sem rastreabilidade. Erro de banco não é classificado como registro inválido. Falha depois de inserir processo deve fazer rollback da página inteira.

## Fora do escopo
Movimentos, quarentena, jobs, checkpoint, triagem e API de produto.

## Evidência e conclusão
Registrar migrations aplicadas, contagens antes/depois de replay e teste de rollback. DONE comprova somente a persistência local de capas.

