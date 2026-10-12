# AgroJud Radar

**Monitor local de contencioso do produtor rural.** O AgroJud Radar descobre, consulta, tria e acompanha processos judiciais públicos potencialmente relacionados ao agronegócio. No ambiente real, permite consultar um, vários ou todos os 33 endpoints DataJud da allowlist: TJs, TJDFT e TRF1 a TRF6.

O sistema organiza evidências para revisão humana. Ele não substitui a consulta ao tribunal nem a análise jurídica.

`React` · `TypeScript` · `FastAPI` · `PostgreSQL` · `Docker Compose`

[Telas](#telas) · [Arquitetura](#arquitetura) · [Demo e real](#demonstração-e-ambiente-real) · [Executar a demo](#executar-a-demonstração) · [Configurar com IA](#configurar-o-ambiente-real-com-uma-ia) · [Como usar](#como-usar) · [Limitações](#limitações) · [Documentação](#documentação)

![Visão geral dos indicadores da base real do AgroJud Radar](docs/evidence/readme/visao-geral.png)

<sub>Captura do ambiente real em 12/10/2026. Os indicadores descrevem somente a base local observada e não representam cobertura dos tribunais.</sub>

## O que o sistema faz

**Coleta**
- Executa consultas assíncronas nos tribunais selecionados. Cada tribunal tem job, progresso e checkpoint próprios; um grupo resume a solicitação e pode terminar parcialmente quando algum membro falha.

**Consolidação**
- Agrupa resultados por número CNJ, preserva as representações de cada origem e exibe movimentações com datas e procedência.

**Revisão humana**
- Oferece triagem com histórico, acompanhamento, novidades, sinais estruturados, indicadores locais e exportação CSV.

## Telas

As capturas foram obtidas no ambiente real do AgroJud em 12/10/2026. Os registros e indicadores refletem a base local naquele momento; não comprovam cobertura geral dos tribunais.

| Processos e sugestões de filtro | Detalhe e movimentações |
| --- | --- |
| ![Lista de processos da base real com filtros](docs/evidence/readme/processos-filtro.png) | ![Detalhe de processo da base real com timeline e painel de triagem humana](docs/evidence/readme/detalhe-processo.png) |
| Lista da base local com filtros e sugestões de valores. | Timeline por origem e painel de triagem humana. |

## Arquitetura

API e worker são processos separados do mesmo backend. O PostgreSQL atende tanto a persistência quanto a fila de jobs, sem infraestrutura adicional.

```mermaid
flowchart LR
  U[Navegador] --> F[Frontend<br/>React + Nginx]
  F --> A[API<br/>FastAPI]
  A --> DB[(PostgreSQL<br/>dados, fila, checkpoints)]
  W[Worker<br/>Python] --> DB
  W --> S{Fonte}
  S -->|demo| FX[Fixtures sintéticas]
  S -->|real| DJ[Endpoints DataJud selecionados]
```

| Parte | Responsabilidade |
| --- | --- |
| Interface | React, TypeScript e Vite; servida por Nginx no Compose. |
| API | FastAPI e Pydantic; o contrato OpenAPI gera os tipos do frontend. |
| Worker | Processo Python separado que consome jobs persistidos. |
| Persistência | PostgreSQL para processos, decisões humanas, fila, leases e checkpoints. |
| Fontes | Fixtures determinísticas em `demo`; integração DataJud separada em `real`, usando os endpoints selecionados da allowlist, sem fallback entre fontes. |

Decisões que orientam o backend:

- Chamadas HTTP externas acontecem fora de transações; página e checkpoint são gravados atomicamente sob posse válida do job.
- Leituras não escrevem, e a ingestão preserva decisões humanas já registradas.
- Falha da fonte é tratada como erro, nunca como resultado vazio.

## Demonstração e ambiente real

Os dois ambientes rodam o mesmo código, mas são isolados por projeto Compose, banco e arquivo de configuração. Não há fallback entre eles: se a fonte real falhar, o sistema não troca para dados sintéticos.

| | Demonstração | Ambiente real |
| --- | --- | --- |
| Finalidade | Conhecer a interface sem credenciais | Consultar processos nos tribunais selecionados da allowlist DataJud |
| Fonte de dados | Fixtures sintéticas determinísticas | API pública DataJud/CNJ |
| Configuração | `.env.demo` (a partir de `.env.demo.example`) | `.env.real` (a partir de `.env.real.example`) |
| Credenciais | Nenhuma externa | Chave DataJud e senha própria do banco |
| Pré-condição | Docker Engine e Docker Compose | Docker, Compose e aceite dos [termos do CNJ](https://datajud-wiki.cnj.jus.br/api-publica/termo-uso/) |

No Radar, a seleção inicial inclui os 33 endpoints; é possível reduzir o escopo para um ou mais tribunais. O runtime usa a mesma chave DataJud configurada no backend e mantém jobs, resultados e falhas separados por tribunal. O suporte no produto não comprova disponibilidade externa em todos os endpoints: a evidência multitribunal continua parcial e não há afirmação de cobertura nacional. Consulte [SPEC-024](specs/SPEC-024-validacao-multitribunal-datajud.md) para o estado atual da validação e [SPEC-025](specs/SPEC-025-suporte-multitribunal.md) para os limites do runtime.

O termo do DataJud descreve o uso não comercial e contém restrições sobre distribuição de informações derivadas. Leia os [termos vigentes](https://datajud-wiki.cnj.jus.br/api-publica/termo-uso/) antes de usar ou publicar esse conteúdo.

## Executar a demonstração

Requer Docker Engine e Docker Compose. O Compose constrói os serviços, então não é necessário instalar Python ou Node.js no host.

```sh
if [ ! -f .env.demo ]; then cp .env.demo.example .env.demo; fi

compose() {
  docker compose --project-name agrojud-demo --env-file .env.demo -f compose.yaml "$@"
}

compose up --detach --wait db
compose run --rm --no-deps api uv run --no-sync alembic upgrade head
compose --profile worker up --build --detach --wait api frontend worker
```

| Serviço | Endereço padrão |
| --- | --- |
| Interface | <http://127.0.0.1:5173> |
| API | <http://127.0.0.1:8000> |
| Documentação interativa da API | <http://127.0.0.1:8000/api/v1/docs> |

Se as portas estiverem ocupadas, ajuste `FRONTEND_PORT` e `API_PORT` em `.env.demo`.

Para parar os serviços sem remover o banco persistente:

```sh
docker compose --project-name agrojud-demo --env-file .env.demo -f compose.yaml --profile worker stop
```

## Configurar o ambiente real com uma IA

O prompt abaixo pode ser colado em uma IA de programação com acesso ao terminal. Ela clona o repositório, lê a documentação do próprio projeto para descobrir como o ambiente real é configurado e deixa o sistema pronto para uso.

Antes de usar, considere:

- **A IA não instala software.** Se faltar Git, Docker, Docker Compose ou outra ferramenta exigida, ela informa o que falta e você mesmo instala.
- **O uso da API DataJud depende dos termos do CNJ.** A IA resume os termos e pede sua confirmação; ela não aceita em seu nome.
- **Segredos ficam locais.** Senha do banco e chave da API são gravadas apenas na configuração do backend e não devem aparecer na conversa.

```text
Clone e configure o AgroJud Radar no ambiente real para eu usar. Execute o trabalho você mesmo; não fique só me dando instruções.

Repositório: https://github.com/Duarte0/AgroJud.git

Regras:
- Não instale, baixe ou atualize programas no computador e não peça senha de administrador. Se faltar algum pré-requisito, diga o que falta e o link oficial de instalação, espere eu instalar e continue de onde parou.
- Use somente o ambiente real. Nunca use a demonstração, fixtures ou dados sintéticos como substituto.
- Nunca mostre senhas, chaves ou o conteúdo do arquivo de configuração real, nem grave segredos em arquivos versionados.
- Preserve dados e alterações existentes: não sobrescreva arquivos, não apague volumes ou bancos e não faça reset. Se algo exigir isso, pare e me pergunte.
- Não esconda falhas. Se uma etapa falhar, explique o erro e o que você tentou.

Passos:
1. Se o diretório atual já for um clone deste repositório, use-o. Caso contrário, clone em uma pasta nova.
2. Antes de executar qualquer coisa, leia README.md, AGENTS.md, a documentação de operação em docs/, as SPEC-024 e SPEC-025, o exemplo de configuração do ambiente real, o compose.yaml e os scripts em scripts/. Descubra a partir deles os pré-requisitos, os comandos, as portas e o procedimento atual para o ambiente real. Não presuma comandos que o repositório não documenta.
3. Verifique se os pré-requisitos encontrados estão disponíveis.
4. Crie a configuração do ambiente real a partir do exemplo, se ainda não existir. Garanta que ela seja ignorada pelo Git e acessível só ao meu usuário. Gere uma senha forte para o banco sem exibi-la. Se já existir um banco real com credenciais diferentes, pare e me avise.
5. Se a chave DataJud não estiver configurada, obtenha a chave pública vigente na página oficial https://datajud-wiki.cnj.jus.br/api-publica/acesso/. Se não conseguir, peça para eu inseri-la diretamente no arquivo.
6. Leia os termos em https://datajud-wiki.cnj.jus.br/api-publica/termo-uso/, resuma para mim os limites de uso, incluindo a finalidade não comercial, e peça minha confirmação explícita. Sem ela, não ative nada que consulte o DataJud.
7. Aplique as migrations e suba os serviços seguindo o procedimento documentado no repositório. Se já existir um banco real, faça antes um backup com a ferramenta do repositório, fora da pasta do projeto. Se portas estiverem ocupadas, ajuste a configuração de forma consistente. Ative o worker somente depois do meu aceite. Não crie consultas nem faça chamadas de teste ao DataJud.
8. Confira os endpoints de saúde da API e a interface. Ao final, informe as URLs, o estado dos serviços e o comando para parar o ambiente sem perder dados.
```

## Como usar

Com o sistema aberto no navegador, o menu lateral se divide em **Coleta** (Radar e Coletas) e **Trabalho** (Visão geral, Processos, Acompanhados e Novidades). O uso típico segue este ciclo:

1. **Iniciar uma coleta no Radar.** Na aba *Nova coleta*, selecione um ou mais tribunais da allowlist (os 33 vêm selecionados inicialmente), escolha um preset temático, a janela de ajuizamento e o limite de registros. O resumo informa o escopo antes do envio. Os mesmos critérios e tribunais podem ser guardados em *Buscas salvas*, com atualização diária opcional às 06h.
2. **Acompanhar o grupo em Coletas.** Cada tribunal tem execução, progresso, checkpoint e erro próprios; o grupo resume o estado geral e mostra conclusão parcial quando membros têm resultados diferentes. Coletas ativas se atualizam sozinhas; conforme o estado, é possível cancelar, retomar, continuar ou reiniciar a varredura.
3. **Explorar a base em Processos.** Os resultados persistidos aparecem agrupados por número CNJ, preservando as representações por tribunal. Filtre por tribunal, número, assunto, tema, classe, órgão julgador, preset, coleta ou categoria de sinal; os campos sugerem valores já existentes na base. O botão *Exportar CSV* exporta todo o recorte filtrado.
4. **Revisar um processo.** O detalhe reúne capas por origem, timeline de movimentações e sinais estruturados (por exemplo, leilão ou recuperação judicial; a regra de penhora fica desabilitada no ambiente real), com link para a movimentação que serviu de evidência. No painel de triagem, classifique o processo como pendente, relevante ou descartado e, se for o caso, confirme manualmente o vínculo rural (a confirmação exige nota). Cada decisão fica no histórico.
5. **Acompanhar processos de interesse.** Use *Acompanhar processo* no detalhe. A página *Acompanhados* lista esses processos, que são atualizados diariamente às 06h ou sob demanda, consultando separadamente os tribunais das representações já conhecidas.
6. **Revisar novidades.** A página *Novidades* mostra o que foi observado depois da referência local de cada processo acompanhado, separando a data original do evento da data em que ele foi observado. Filtre por situação e categoria e marque cada item como revisado.
7. **Consultar a Visão geral.** Os indicadores descrevem apenas a amostra salva localmente e os tribunais selecionados, sem medir risco jurídico nem o total de processos de um tribunal ou do país.

As ações que consultam o DataJud (coletas, atualizações e acompanhamento diário) dependem do worker ativo e, no ambiente real, de chave configurada. Quando a fonte não está disponível, a interface informa o motivo em vez de exibir resultado vazio.

## Limitações

- **Uso local e individual.** O projeto opera localmente para um usuário e não tem autenticação.
- **Sem autorização de uso comercial.** O projeto não declara autorização para uso profissional ou comercial. O uso dos dados é regido pelas orientações de [acesso](https://datajud-wiki.cnj.jus.br/api-publica/acesso/) e pelos [termos vigentes do CNJ](https://datajud-wiki.cnj.jus.br/api-publica/termo-uso/).
- **Cobertura não garantida.** Uma coleta concluída descreve aquela consulta; não comprova cobertura integral nem atualização completa do tribunal. A validação externa multitribunal permanece parcial, conforme [SPEC-024](specs/SPEC-024-validacao-multitribunal-datajud.md); suporte a um endpoint na interface não prova que ele esteja disponível.
- **Regra de penhora desabilitada no real.** As evidências técnicas e seus limites estão na [SPEC-020](specs/SPEC-020-operacao-e-aceite-integrado.md).
- **Configuração própria.** O modo real é separado da demonstração e exige configuração própria no backend.

## Documentação

| Documento | Conteúdo |
| --- | --- |
| [PRD](PRD.md) | Requisitos do produto |
| [Plano de implementação](IMPLEMENTATION_PLAN.md) | Fases e contratos de implementação |
| [Índice das SPECs](specs/README.md) | Especificações, dependências, status e evidências |
| [SPEC-024 — Validação multitribunal](specs/SPEC-024-validacao-multitribunal-datajud.md) | Evidências externas parciais por tribunal e preset |
| [SPEC-025 — Suporte multitribunal](specs/SPEC-025-suporte-multitribunal.md) | Seleção de tribunais, grupos e limites do runtime |
| [Operação e aceite integrado](docs/operacao-e-aceite-integrado.md) | Migrations, jobs, backup, testes e recuperação |
| [Frontend](frontend/README.md) | Detalhes do frontend e do contrato OpenAPI |
