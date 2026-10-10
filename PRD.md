# AgroJud Radar

Documento de requisitos de produto — MVP

- Data: 08/10/2026.
- Status: escopo definido em entrevista; integração real e regras temáticas ainda não validadas.
- Entrega deste documento: definição do produto, sem representar implementação ou prontidão operacional.

## 1. Visão do produto

O AgroJud Radar é um sistema local de descoberta, triagem e acompanhamento de processos públicos, com foco no contencioso relacionado ao agronegócio. Usa metadados e movimentações da API pública DataJud/CNJ para organizar uma base consultável e destacar informações que merecem revisão humana.

A pergunta central é: **quais processos e movimentações da base coletada merecem revisão do escritório, e por quê?**

O projeto será apresentado como portfólio para uma vaga de Analista de IA e Dados em escritório especializado em agronegócio. Seu diferencial será a engenharia de coleta: integração rastreável, recuperação após falhas, processamento idempotente e transparência sobre a qualidade dos dados.

### Problema e público

O usuário inicial é um analista ou advogado que precisa pesquisar processos potencialmente relevantes, avaliar sua relação com o contexto rural e acompanhar os selecionados. Consultas isoladas não oferecem uma base organizada, histórico de observação ou visibilidade das falhas de coleta.

O produto deve reduzir o trabalho de organizar resultados e revisar novidades. Não substitui a consulta ao tribunal, a leitura dos autos ou o julgamento jurídico do profissional.

### Resultado esperado

Uma demonstração completa deve permitir iniciar uma coleta, acompanhar seu progresso, interromper e retomar o worker, revisar resultados, selecionar processos para acompanhamento, observar atualizações e exportar dados. Cada sinal deve ter uma justificativa verificável.

## 2. Decisões e premissas

### Confirmado na entrevista

| Decisão | Definição |
| --- | --- |
| Fluxo principal | Radar com triagem humana |
| Tribunal inicial | TJGO |
| Abrangência temática | Assuntos rurais e descoberta ampla de execuções e recuperação judicial |
| Operação | Local, um operador, sem login |
| Atualização | Manual e diária enquanto o ambiente estiver ligado |
| Escopo | MVP enxuto, priorizando confiabilidade da coleta |

### Defaults adotados no plano

| Parâmetro | Default |
| --- | --- |
| Janela inicial de descoberta | Últimos 12 meses de ajuizamento, editável |
| Tamanho de página remota | 100 registros |
| Limite por execução de descoberta | 2.000 registros de origem; ao atingir o limite, marcar parcial e permitir continuação |
| Concorrência inicial | Um worker, uma requisição remota por vez |
| Acompanhamento | Uma lista chamada “Acompanhados” |
| Exportação | CSV |
| Agendamento | Atualização diária de presets salvos e acompanhados |
| Demonstração sem fonte disponível | Base sintética separada e claramente identificada |

Esses defaults são escolhas de produto, não limites oficiais da API. Devem ser configuráveis pelo backend quando aplicável e documentados. A janela por ajuizamento pode excluir processos antigos ainda relevantes; o filtro deve permanecer visível e editável. Processos acompanhados serão consultados independentemente dessa janela.

### Pesquisa e limites da fonte

- A API pública oferece metadados de capas e movimentações; não deve ser tratada como fonte de documentos, textos integrais ou busca por partes/CPF/CNPJ [1][2].
- O identificador de origem inclui tribunal, classe, grau, órgão julgador e número. Um processo pode ter representações distintas; agrupar não significa descartar essas diferenças [2].
- O exemplo oficial de paginação utiliza `search_after` com ordenação por `@timestamp`. Sua existência não comprova desempate estável, snapshot consistente ou retomada sem lacunas em uma fonte mutável [3].
- A atualidade e a completude dos dados não são garantidas pelo CNJ. Mostrar quando o sistema consultou a fonte não equivale a garantir que ela esteja atualizada [4].
- O termo publicado contém restrições de uso comercial e de exploração das informações derivadas. O projeto de portfólio não estabelece autorização para operação comercial pelo escritório ou republicação de dados [4]. A adoção profissional exige avaliação específica da finalidade e dos termos vigentes.

## 3. Escopo funcional

### 3.1. Radar e descoberta

O operador seleciona um preset, ajusta filtros e inicia uma coleta assíncrona. O frontend recebe o identificador do job e passa a consultar seu estado e os resultados persistidos.

- Presets versionados para assuntos rurais, execuções e recuperação judicial.
- Filtros por período de ajuizamento, classe, assunto e órgão julgador, sempre limitados ao TJGO no MVP.
- Mostrar os critérios efetivos, a versão do preset, a data da consulta e o estado de cobertura.
- Não oferecer consultas Elasticsearch arbitrárias na interface.
- Separar “nenhum resultado”, “fonte indisponível”, “coleta em andamento” e “resultado parcial”.
- Manter a associação entre resultados e coletas para explicar de onde vieram e quais critérios os selecionaram.

Crédito rural e contratos rurais orientam os presets específicos. Execuções e recuperação judicial amplas poderão encontrar processos sem relação com o agronegócio. Esses resultados devem entrar como **vínculo rural não confirmado**, nunca como produtores rurais identificados automaticamente.

Os códigos TPU e a abrangência de cada preset serão validados e versionados antes de sua ativação. Nomes de temas neste PRD não constituem um catálogo técnico já validado.

### 3.2. Triagem de processos

Cada candidato apresenta número processual, dados disponíveis da capa, assuntos, motivo da captura, sinais estruturados e origem da informação.

- Decisão humana: pendente, relevante ou descartado.
- Vínculo rural: não confirmado ou confirmado manualmente, com nota justificativa quando confirmado.
- Permitir nota de revisão e reversão da decisão.
- Registrar histórico de mudanças com data e valores anteriores, sem inventar identidade de usuário em uma aplicação sem login.
- Não confundir “relevante para revisão” com “vínculo rural confirmado”.
- Preservar decisões e notas durante novas coletas e reprocessamentos.
- Adicionar à lista de acompanhamento será uma ação explícita, independente da decisão de triagem.

### 3.3. Lista e detalhes de processos

- Lista local paginada, com pesquisa por número CNJ e filtros por tema, classe, órgão, decisão de triagem, vínculo rural e acompanhamento.
- Todos os filtros textuais de Processos devem aceitar texto livre e oferecer sugestões dos valores encontrados na base local inteira do ambiente atual. As sugestões acompanham a digitação, ignoram diferenças de caixa e acentuação e não são limitadas pelos demais filtros ativos. Seletores finitos de triagem, vínculo rural, acompanhamento e novidades permanecem seletores.
- O filtro de coleta deve mostrar data, tema e estado da execução; seu identificador interno permanece compatível com URLs e consultas existentes, sem aparecer no campo, no chip ou no resumo dos filtros.
- Uma entrada agrupada por número CNJ, preservando as representações da fonte no detalhe.
- Exibir capas por origem/grau/órgão; divergências não serão ocultadas por uma mesclagem silenciosa.
- Timeline cronológica com código, descrição, complementos disponíveis e origem de cada movimento.
- Diferenciar data do movimento, atualização informada pela fonte e primeira observação local.
- Campo ausente será indicado como não informado; não inferir estado jurídico atual a partir da última movimentação.

### 3.4. Acompanhados e novidades

- Uma lista “Acompanhados”, com inclusão e remoção manual de processos já coletados.
- Atualização manual e diária por número processual, independentemente da janela do radar.
- A primeira captura estabelece a referência histórica; movimentos já existentes não devem gerar uma fila artificial de novidades.
- Após essa referência, movimentos recém-observados entram na caixa de novidades com estado pendente ou revisado.
- Um movimento antigo recebido agora deve ser identificado como recém-observado, sem ser apresentado como ocorrido hoje.
- Recoletas idênticas não devem recriar novidades nem apagar sua revisão.
- Resultado vazio em uma atualização não remove o processo nem prova encerramento ou sigilo; registrar a ausência na consulta e preservar o histórico.

### 3.5. Classificação explicável

Regras determinísticas usarão códigos TPU e complementos estruturados disponíveis. O conjunto inicial buscará sinais de penhora, leilão e recuperação judicial, condicionado à validação dos códigos e dos payloads reais.

Cada sinal deve guardar categoria, regra e versão, justificativa, movimento ou atributo de origem e data de processamento. Não haverá pontuação preditiva de risco jurídico, urgência automática, interpretação de decisão ou inferência de prazo.

Reprocessar regras locais será uma operação distinta de recoletar no DataJud. Deve preservar as decisões humanas e o histórico de classificação, sem multiplicar sinais equivalentes. Correções e ambiguidades da fonte permanecerão rastreáveis.

### 3.6. Visão geral e exportação

Indicadores limitados à base persistida:

- Processos distintos e representações de origem, contados separadamente.
- Pendências e decisões de triagem.
- Processos acompanhados e novidades pendentes.
- Distribuição por temas e categorias de sinais.
- Últimas coletas, falhas e datas de observação.

Todo indicador deve explicitar seu recorte. Não apresentar a amostra como total do contencioso rural do TJGO, nem calcular taxa de êxito ou duração jurídica sem metodologia validada.

O CSV respeitará os filtros da lista e incluirá número, tribunal, dados de classificação disponíveis, decisão humana, motivo da captura e datas de observação. Deve usar UTF-8, escape correto de campos e neutralização de conteúdo interpretável como fórmula por planilhas. Não exportar payload bruto completo por padrão.

### 3.7. Tela de coletas

Exibir tipo, filtros, origem, início/fim, última atividade, páginas confirmadas, registros recebidos, novos, atualizados, inalterados e rejeitados, retries, erro resumido e possibilidade de ação.

- Estados: aguardando, executando, aguardando retry, concluído, parcial, falhou e cancelado.
- Não mostrar porcentagem exata quando o total remoto for apenas estimado ou desconhecido.
- Permitir cancelamento cooperativo e retomada a partir do último checkpoint válido.
- Atingir o limite configurado deve produzir estado parcial, com opção explícita de continuar.
- Falha após páginas persistidas deve preservar e identificar os resultados parciais.
- Diferenciar retomada da mesma consulta de uma nova coleta com critérios alterados.

## 4. Arquitetura

```text
Frontend React + TypeScript
            |
       API FastAPI
            |
       PostgreSQL <---- Worker Python ----> DataJud/CNJ
```

### Componentes

| Componente | Responsabilidade e tecnologia |
| --- | --- |
| Frontend | React, TypeScript, Vite, React Router, TanStack Query, Tailwind e shadcn/ui; Recharts somente onde facilitar leitura |
| API | FastAPI e Pydantic; consulta local, triagem, acompanhamento, jobs, exportação e OpenAPI |
| Persistência | PostgreSQL, SQLAlchemy e Alembic; dados normalizados, JSONB, jobs e checkpoints |
| Worker | Processo Python separado, HTTPX para integração; coleta, retries, atualização e reprocessamento |
| Infraestrutura | Docker e Docker Compose; frontend, API, worker e banco com volume persistente |

API e worker compartilham os serviços de domínio e contratos de persistência, sem depender da API HTTP interna para cada gravação. A coleta não será executada como tarefa em memória do processo FastAPI.

Não incluir Redis, Celery, RabbitMQ, serviço de busca dedicado ou infraestrutura de observabilidade externa no MVP. PostgreSQL é suficiente para a fila inicial e consultas estruturadas previstas.

### Interfaces públicas

- API versionada em `/api/v1`.
- Recursos para processos e representações, timeline, triagem, acompanhados, novidades, indicadores, jobs e exportações.
- Comandos de coleta retornam HTTP 202 e identificador de job persistido; não esperam a execução remota terminar.
- Listagens paginadas, filtros tipados e respostas de erro estruturadas com código e mensagem compreensível.
- Ações explícitas para cancelar, retomar e continuar jobs, com validação de estado.
- OpenAPI como fonte do contrato; gerar tipos TypeScript e verificar divergências na integração contínua.
- Endpoints de liveness/readiness; saúde da aplicação e disponibilidade externa do DataJud serão indicadores distintos.

## 5. Engenharia de coleta

### 5.1. Contrato real antes da ativação

A primeira etapa técnica deverá validar uma consulta limitada no TJGO: autenticação, filtros, formatos de campos, ordenação, desempate, segunda página e comportamento dos identificadores. Registrar data, parâmetros e evidências sanitizadas, sem credenciais.

Validar os códigos TPU não basta para comprovar que a consulta retorna esses dados corretamente no TJGO. Timeout é indisponibilidade, não resultado vazio. A implementação pode avançar com fixtures, mas a integração real só será declarada validada após evidência do endpoint público.

### 5.2. Jobs persistentes

- Reservar jobs atomicamente no PostgreSQL, usando bloqueio transacional adequado, como `FOR UPDATE SKIP LOCKED`.
- Registrar lease, heartbeat e token de posse; gravações devem verificar que o worker ainda possui a reserva.
- Recuperar jobs abandonados após expiração da lease, sem permitir que um worker antigo continue gravando.
- Não manter transação ou bloqueio de banco aberto durante a requisição HTTP remota.
- Persistir consulta, filtros, versão, ordenação, limites e cursor utilizados.
- Preservar tentativas e erros sanitizados para diagnóstico.

### 5.3. Checkpoint e idempotência

A confirmação dos registros de uma página e o avanço de seu checkpoint devem ocorrer na mesma transação. Se houver falha antes do commit, a página será repetida. Se o commit foi concluído, a retomada parte do checkpoint confirmado.

A garantia pretendida é processamento com possíveis repetições e efeitos locais idempotentes, não entrega exatamente uma vez pelo DataJud.

- Repetir uma página não duplica representações, versões idênticas, sinais ou novidades.
- Um registro inválido deve ser preservado em quarentena rastreável antes de permitir o avanço da página; sua rejeição fica visível no job.
- Erro de persistência ou de estrutura da página impede avanço do checkpoint.
- Job com rejeições não pode ser apresentado como sucesso integral.
- Retomada mantém os critérios originais; alteração de filtros cria outra coleta.

### 5.4. Paginação e fonte mutável

Usar `search_after` com ordenação e desempate validados no endpoint público. Não copiar garantias de APIs restritas ou presumir suporte a snapshot/PIT.

Mesmo com cursor determinístico, mudanças na fonte podem deslocar registros durante a coleta. Atualizações diárias devem revisitar os recortes salvos e os números acompanhados, com deduplicação local, em vez de depender exclusivamente de um cursor incremental permanente.

Não declarar cobertura total quando houver limite, falha, cursor inválido ou mutação não reconciliada. Se a retomada exata não for segura, manter o checkpoint como evidência e iniciar uma nova varredura identificada, aproveitando a idempotência local. Nunca reiniciar silenciosamente apresentando isso como continuação exata.

### 5.5. Erros, limites e agendamento

- Timeouts e falhas transitórias de rede/HTTP 429/5xx recebem retries limitados, backoff exponencial e jitter; respeitar `Retry-After` quando presente.
- Erros de autenticação, autorização e consulta inválida não entram em retry cego.
- Persistir o horário da próxima tentativa para que reinícios não zerem o controle de retry.
- Configurar timeouts, orçamento de tentativas e espaçamento entre requisições; não tratar esses parâmetros como limites oficiais publicados.
- Agendamento diário persistido no banco, com chave que impeça duplicação do mesmo disparo.
- Após desligamento, executar uma atualização vencida por alvo, sem acumular todas as execuções perdidas.
- Uma atualização equivalente já em andamento não deve ganhar uma duplicata por clique manual ou disparo diário.

## 6. Modelo conceitual e qualidade dos dados

| Entidade | Finalidade |
| --- | --- |
| Processo | Agrupamento local por número CNJ, preservado como texto |
| Representação de origem | Registro distinto do DataJud, com identificador, tribunal, classe, grau e órgão |
| Versão de payload | Conteúdo bruto JSONB distinto por hash, origem, datas e vínculo à coleta |
| Movimento observado | Estrutura normalizada e rastreável à representação e versão de origem |
| Preset | Filtros temáticos e versão do catálogo de códigos |
| Job, tentativa e checkpoint | Execução, posse, retries, progresso e retomada |
| Resultado de coleta | Associação entre consulta e registros encontrados |
| Quarentena | Registro ou resposta rejeitada e motivo de validação |
| Triagem e histórico | Decisões humanas, notas e alterações |
| Acompanhamento e novidade | Seleção de processos e revisão de mudanças observadas |
| Sinal | Resultado de regra com versão e evidência |

Não assumir identificador global de movimentação. A identidade técnica deve considerar representação, conteúdo normalizado e multiplicidade de ocorrências indistinguíveis. Deduplicar apenas por código e data pode apagar eventos legítimos; unir movimentos de capas diferentes pode produzir falsas equivalências.

Preservar versões permite auditar correções da fonte. Uma mudança de descrição não será automaticamente tratada como novo ato jurídico. A política técnica local de reconciliação foi documentada e testada com fixtures sintéticas na SPEC-004, cobrindo reordenação, repetição, correção e ausência de identificadores, mantendo incertezas visíveis. Isso não valida a forma ou estabilidade de movimentos reais do DataJud.

Valores ausentes serão distintos de zero ou string vazia. Horários serão armazenados com tratamento explícito de fuso e exibidos em `America/Sao_Paulo`; preservar o valor original quando houver ambiguidade de origem.

Índices devem atender número CNJ, identidade da origem, filtros de lista, movimentos por processo/data e seleção de jobs por estado/próxima tentativa. Não indexar indiscriminadamente todo o JSONB.

## 7. Experiência, operação e limites

### Interface

Navegação principal: visão geral, radar/triagem, processos, acompanhados/novidades e coletas. Priorizar legibilidade de tabelas, filtros visíveis, estados vazios explicativos, carregamento e erros acionáveis. Desktop é o alvo principal, com funcionamento em telas menores e controles acessíveis por teclado.

Metadados técnicos detalhados ficam na tela de coletas e nas evidências, sem dominar o fluxo do advogado. A origem e as datas relevantes devem permanecer acessíveis onde influenciam a interpretação.

### Operação local

- Publicar serviços somente em localhost; PostgreSQL não precisa de porta pública.
- Sem autenticação apenas no escopo local de um operador. Exposição em rede ou uso por equipe requer nova definição de segurança.
- Credencial DataJud somente no backend/worker por configuração de ambiente; nunca no frontend, repositório ou logs.
- Banco persistente e migrações versionadas; documentar backup, restauração e reset explícito do ambiente demonstrativo.
- Logs estruturados com job, tentativa, página, duração e categoria de erro, sem payloads completos ou credenciais por padrão.
- Fixtures sintéticas isoladas da base real, com indicação inequívoca na interface e nas exportações.

### Riscos e respostas

| Risco | Resposta do produto |
| --- | --- |
| Muitos falsos positivos em execuções/RJ | Separar vínculo rural, explicar captura e oferecer triagem humana |
| Fonte atrasada ou incompleta | Exibir datas, limites e cobertura observada; não prometer monitoramento em tempo real |
| Indisponibilidade do DataJud | Estado de falha explícito, retries limitados e demonstração sintética separada |
| Perda de resultados entre páginas | Validar ordenação, manter checkpoints, revisitar recortes e documentar limites da fonte mutável |
| Duplicação ou fusão indevida | Separar processo e representações; preservar versões e multiplicidade de movimentos |
| Volume crescente de payloads | Guardar versões distintas por hash e medir crescimento; sem purga silenciosa no MVP |
| Confusão entre sinal e conclusão jurídica | Evidência e regra visíveis, sem inferência de prazo, urgência ou vínculo rural |
| Uso incompatível com termos da fonte | Separar portfólio de adoção profissional e verificar condições antes dessa adoção |

## 8. Fora do MVP

- IA generativa, embeddings, RAG e análise de documentos jurídicos.
- Download de peças, scraping de tribunais ou fontes privadas.
- Pesquisa por nomes de partes, CPF/CNPJ e identificação automática de produtores.
- Controle de prazos, agenda jurídica, recomendação processual ou previsão de decisões.
- CRM, prospecção comercial automatizada e gestão financeira.
- Múltiplos usuários, permissões, autenticação e exposição pública.
- Múltiplos tribunais, múltiplas watchlists e notificações por e-mail/WhatsApp.
- XLSX, dashboards extensos, infraestrutura distribuída e ferramentas adicionadas apenas por aparência de complexidade.

Esses itens não entram implicitamente como dependência das funcionalidades do MVP.

## 9. Validação e critérios de aceitação

### Coleta e persistência

1. Coletar pelo menos duas páginas reais do TJGO com evidência sanitizada do contrato e da ordenação, ou registrar explicitamente o bloqueio externo sem alegar integração concluída.
2. Interromper antes do commit e comprovar repetição segura; interromper depois do commit e comprovar retomada do checkpoint confirmado.
3. Repetir uma página sem aumentar indevidamente representações, versões idênticas, sinais ou novidades.
4. Testar HTTP 429 com `Retry-After`, 5xx, timeout, autenticação inválida e resposta malformada.
5. Expirar a lease, recuperar o job e impedir gravação pelo worker antigo.
6. Preservar capas distintas e ocorrências legítimas de movimentos iguais; testar reordenação e correção de payload.
7. Impedir avanço em falha de banco; tornar rejeições visíveis e rastreáveis.
8. Distinguir sucesso vazio, falha sem dados e falha após resultados parciais.
9. Atingir limite, exibir parcial e continuar sem perder o contexto da consulta.
10. Evitar duplicação de agendamento e execução equivalente após reinício.

### Fluxo de produto

1. Iniciar coleta pela interface, acompanhar progresso e abrir um resultado persistido.
2. Revisar candidato, registrar nota, confirmar vínculo rural quando aplicável e adicioná-lo aos acompanhados.
3. Recoletar e preservar decisões; observar somente novidades posteriores à referência inicial.
4. Reprocessar regras sem acesso remoto e sem apagar revisão humana.
5. Exportar CSV consistente com os filtros, incluindo procedência e datas.
6. Reiniciar Compose e preservar dados, decisões, jobs e checkpoints.
7. Distinguir claramente dados sintéticos e reais, inclusive em exportações.

### Estratégia de testes

- pytest para regras, normalização, identidade técnica, classificação de erros e políticas de retry.
- Integração com PostgreSQL real para transações, unicidade, checkpoints, leases e migrações.
- HTTP simulado e fixtures para falhas determinísticas; suíte comum sem dependência da disponibilidade do CNJ.
- Testes de navegador para o fluxo principal de coleta, triagem, acompanhamento e exportação.
- Verificação real do DataJud separada, executada de forma limitada e com registro de evidência.
- Build e verificação de tipos do frontend, consistência OpenAPI/TypeScript e validação de inicialização do Compose.

Não definir metas arbitrárias de throughput antes de medir. Registrar tamanho da amostra, duração, páginas, retries e consumo observado em uma coleta controlada; usar esses dados para ajustar limites e avaliar desempenho.

## 10. Etapas de entrega e definição de pronto

### Etapa 1 — Contrato e recorte

Validar TJGO, paginação, identidade, códigos TPU e exemplos de sinais. Documentar limitações e decisões técnicas com evidências. Preparar fixtures sintéticas para cenários reproduzíveis.

### Etapa 2 — Núcleo confiável

Entregar migrations, persistência, worker, jobs, checkpoints, retries, leases, idempotência, reprocessamento e testes de falha. Esta etapa deve permitir demonstrar retomada antes de investir em dashboards.

### Etapa 3 — Fluxo jurídico

Entregar radar, triagem, lista/detalhe, timeline, acompanhados e novidades, com integração tipada e estados operacionais claros.

### Etapa 4 — Apresentação e operação

Completar visão geral, CSV, agendamento diário, Compose, testes do fluxo e documentação. Preparar roteiro que demonstre uma falha e sua recuperação, além do caminho de sucesso.

### Definição de pronto

O MVP estará tecnicamente pronto quando os critérios automatizados passarem, a instalação local for reproduzível e o fluxo completo funcionar com persistência entre reinícios. A integração real exige sua própria evidência; testes sintéticos não a substituem. Adoção profissional é uma decisão posterior, distinta da conclusão técnica.

Documentação de entrega: README de instalação, configuração de ambiente, arquitetura, modelo de dados, contrato OpenAPI, operação e recuperação de jobs, regras de classificação, limites da fonte, evidências de validação e roteiro de demonstração.

## 11. Fontes e pendências de pesquisa

Fontes consultadas durante o planejamento em 08/10/2026:

1. [CNJ — API pública DataJud](https://www.cnj.jus.br/sistemas/datajud/api-publica/).
2. [DataJud Wiki — Glossário de dados](https://datajud-wiki.cnj.jus.br/api-publica/glossario/).
3. [DataJud Wiki — Pesquisa com paginação](https://datajud-wiki.cnj.jus.br/api-publica/exemplos/exemplo3/).
4. [DataJud Wiki — Termo de uso](https://datajud-wiki.cnj.jus.br/api-publica/termo-uso/).
5. [DataJud Wiki — Endpoints](https://datajud-wiki.cnj.jus.br/api-publica/endpoints/).
6. [DataJud Wiki — Pesquisa por número processual](https://datajud-wiki.cnj.jus.br/api-publica/exemplos/exemplo1/).

Permanecem pendências técnicas de implementação: comportamento real do TJGO, desempate aceito na paginação, evidência de reconciliação em amostras reais e catálogo TPU versionado. Nenhum endpoint real foi validado como parte da redação deste PRD. Indisponibilidade dessas verificações deve ser registrada como limitação, sem transformar hipótese em requisito comprovado.
