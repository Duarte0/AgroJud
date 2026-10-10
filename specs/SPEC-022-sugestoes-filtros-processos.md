# SPEC-022 — Sugestões pesquisáveis nos filtros de Processos

Status: DONE

Data: 10/10/2026

Dependências: [SPEC-011 — API operacional e OpenAPI](SPEC-011-api-operacional-e-openapi.md), [SPEC-012 — Frontend de coleta e consulta](SPEC-012-frontend-de-coleta-e-consulta.md), [SPEC-021 — Redesign do frontend](SPEC-021-redesign-frontend.md)

## Objetivo

Permitir que a pessoa encontre valores já presentes na base local enquanto preenche qualquer filtro textual da página Processos. O texto livre continua aceito; selecionar uma sugestão preenche o valor canônico usado pelo filtro existente.

## Decisões confirmadas

- As sugestões vêm da base local inteira do ambiente atual e não variam conforme os demais filtros ativos.
- A correspondência durante a sugestão ignora caixa e acentuação.
- Listas sem busca mostram no máximo 10 valores comuns; a pesquisa digitada mostra no máximo 20 correspondências. Número CNJ só sugere após o início da digitação.
- Os seletores finitos de triagem, vínculo rural, acompanhamento e novidades permanecem seletores.
- O filtro de coleta mantém `collection_id` como valor interno e parâmetro de URL para preservar semântica e links existentes. A interface mostra data, preset/consulta e status, nunca o UUID no campo, chip ou resumo de filtros.
- Não há migration nem mudança na semântica dos filtros atuais.

## Escopo

### API

- Adicionar `GET /api/v1/process-filter-options` com `field`, `q` e `selected_value` opcionais conforme o campo.
- Responder itens `{value, label, detail?, process_count?}` e limitar texto e quantidade de resultados.
- Cobrir os campos textuais: `process_number`, `subject`, `subject_code`, `subject_name_exact`, `class`, `court_unit`, `preset_id`, `collection_id` e `signal_category`.
- Consultar somente dados persistidos locais e respeitar o ambiente da aplicação. A rota é somente leitura, não chama DataJud e não inclui os filtros atuais da lista no cálculo das sugestões.
- Para coleta, retornar o valor UUID compatível com o filtro e um rótulo humano composto por data local, preset/consulta e status da execução. Manter a resolução de URLs legadas.
- Expor o contrato no OpenAPI e gerar os tipos do frontend.

### Interface

- Transformar os nove campos textuais de Processos em comboboxes acessíveis por teclado, com correspondências atualizadas enquanto a pessoa digita e opção de texto livre.
- Selecionar uma sugestão aplica seu valor canônico; continuar digitando mantém o uso livre dos filtros existentes.
- Para coleta, é necessário selecionar um resultado para aplicar o filtro, pois o identificador interno não é digitado nem exibido.
- Resolver o rótulo de coleta em Processos e no resumo da Visão geral, inclusive quando a página é aberta por um link antigo com UUID.
- Manter os quatro filtros finitos como seletores.

## Critérios de aceite

1. Cada campo textual pode ser preenchido livremente e oferece sugestões ao receber foco ou texto.
2. A busca das sugestões é parcial, case-insensitive e accent-insensitive; uma seleção grava o `value` retornado, não o rótulo formatado.
3. Opções vêm da base local inteira do ambiente atual mesmo quando os filtros ativos retornariam zero processos.
4. O resultado vazio ou falha da API é apresentado sem bloquear texto livre nos campos comuns.
5. Sugestões padrão têm até 10 itens; a busca digitada, até 20; CNJ não lista valores sem termo.
6. Decisão, vínculo rural, acompanhamento e novidades continuam seletores nativos.
7. A coleta é pesquisável por metadados; o UUID não aparece na interface visível, e links com `collection_id` antigo continuam selecionando a coleta e filtrando o mesmo resultado.
8. Filtros existentes, links da Visão geral, exportação CSV e contagens preservam valores e semântica.
9. Testes backend comprovam opções, ambiente, acentos, limite, compatibilidade de coleta e ausência de escritas. Testes frontend e Playwright comprovam digitação, seleção, teclado, rótulos e responsividade.
10. Ruff, mypy, testes backend/frontend, lint, typecheck, build e OpenAPI passam em ambientes isolados. Nenhum dado ou volume operacional é reiniciado.

## Exclusões e limites

- Sem filtros novos, mudança de semântica, migration, chamada externa ou fallback para fonte sintética.
- Sugestões representam apenas o que já está persistido localmente; não comprovam cobertura do universo do tribunal.
- A busca digitada pode não produzir opção, mas campos diferentes de coleta continuam aceitando o texto digitado conforme o contrato preexistente.

## Evidências

- PostgreSQL sintético isolado (`compose.test.yaml`, projeto `agrojud-spec022-test`, porta local 59022): `pytest` — **300 passed**, com um aviso de depreciação Starlette já existente. Sem reset ou acesso aos dados operacionais.
- Backend: Ruff, `ruff format --check` e mypy — aprovados.
- Frontend: `npm --prefix frontend test` — **60 passed**; lint, typecheck e build — aprovados.
- OpenAPI: geração executada e `npm --prefix frontend run openapi:check` — aprovado; tipos e hooks gerados para o endpoint de opções.
- Playwright, `./scripts/e2e.sh` com projeto `agrojud-spec022-e2e`, API `18777`, frontend `18778` e banco descartável — **22 passed**. Inclui digitação/teclado, seleção canônica, compatibilidade de `collection_id`, rótulos na Visão geral, exportação e fluxos relacionados. O ajuste do teste espera a navegação de detalhe antes de extrair o ID; o teste de exportação expande os filtros avançados antes de verificar o combobox.
- Inspeção visual das capturas: [autocomplete de assunto](/tmp/agrojud-spec022-evidence/spec022-autocomplete-assunto.png), [filtros aplicados sem UUID visível](/tmp/agrojud-spec022-evidence/spec022-processos-filtros-aplicados.png) e [coleta rotulada na Visão geral](/tmp/agrojud-spec022-evidence/spec022-sugestoes-filtros-processos.png). Capturas fora do repositório.
- Nenhuma migration; nenhum teste utilizou credencial, fonte ou volume operacional.
