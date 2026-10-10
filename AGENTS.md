# Repository Guidelines

## Tarefas e escopo

Leia a SPEC ativa, suas dependências e os contratos pertinentes no `PRD.md` e no `IMPLEMENTATION_PLAN.md`. Confirme o estado no código e nas evidências, pois status documental não prova implementação. Limite-se ao escopo solicitado; registre conflitos sem contorná-los nem antecipar trabalho adiado.

## Arquitetura e limites

API e worker são processos separados do mesmo backend; PostgreSQL atende persistência e fila. Não acrescente infraestrutura fora do escopo. Alinhe frontend e API pelo OpenAPI e use tipos gerados quando disponíveis. Faça HTTP externo fora de transações e grave página/checkpoint atomicamente sob posse válida. Leituras não escrevem; ingestão preserva decisões humanas. Falha da fonte não é resultado vazio, e fixture não valida fonte real.

## Qualidade de implementação

Planeje responsabilidades dos módulos antes de criar arquivos. Siga padrões configurados e convenções de React/TypeScript, FastAPI/Python, SQLAlchemy e PostgreSQL. Mantenha código coeso e tipado; valide entradas, classifique erros e use logs estruturados sem dados sensíveis. Prefira bibliotecas consolidadas quando simplificarem; evite abstrações prematuras e otimize com base em medição. Preserve acessibilidade, responsividade e consistência visual. Em caso de dúvida, consulte documentação oficial e fontes primárias; compare opções e registre decisões arquiteturais relevantes. Use plugins e ferramentas quando trouxerem ganho concreto.

## Testes e validação

Inclua testes com cada unidade. Antes de concluir, execute testes, lint, typecheck, build e verificações pertinentes de banco/containers. Use PostgreSQL isolado e HTTP simulado; não use dados operacionais. Probes externos devem ser limitadas, separadas e documentadas. Relate comandos, resultados e limites; não esconda falhas com skips nem conclua com erros do escopo.

## Banco e migrations

Imponha invariantes com constraints e crie índices para consultas demonstradas. Faça migrations incrementais da capacidade ativa, execute-as explicitamente e valide banco vazio e versões anteriores suportadas. Isole bancos e volumes de teste; não faça reset nem remova volumes para contornar falhas.

## Segurança e documentação

Leia credenciais da configuração de ambiente no backend. Nunca grave segredos no Git, frontend, fixtures públicas ou logs. Mantenha ambiente/fonte explícitos, sem fallback entre dados sintéticos e reais após erro. Atualize o documento dono da decisão (`PRD.md`, plano ou SPEC) e reflita mudanças de dependência/status em `specs/README.md`; não duplique requisitos. Registre o estado e as limitações reais.

## Git

Confira `git status` e diffs antes/depois; preserve alterações alheias, faça staging explícito e revise o conteúdo staged. Mantenha commits pequenos e coerentes; prefira Conventional Commits (`docs:`, `feat:`, `fix:`). Não reescreva histórico compartilhado sem instrução.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, use the installed graphify skill or instructions before doing anything else.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
