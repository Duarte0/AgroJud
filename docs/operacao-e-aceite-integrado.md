# Operação e aceite integrado

Este runbook cobre a instalação local, a operação dos serviços e a demonstração sintética do M9. Ele não aprova a integração DataJud/TJGO nem autoriza uso profissional ou comercial.

## Arquitetura e limites dos dados

- O container `frontend` serve o build React com Nginx. Rotas da aplicação usam fallback para `index.html`; `/api/` é encaminhado internamente para `api:8000`.
- `api` e `worker` são processos separados da mesma imagem Python. O worker reserva jobs no PostgreSQL e não chama a API local para persistir resultados.
- PostgreSQL mantém entidades, decisões humanas, jobs, leases e checkpoints no volume nomeado pelo projeto Compose. Migrações são um comando explícito; a readiness falha enquanto o schema não estiver no head.
- `agrojud-demo` usa somente fixtures sintéticas. `agrojud-real` seleciona exclusivamente DataJud. Os nomes de projeto Compose e volumes isolam as duas bases; falha real nunca aciona fixture.
- O browser-test usa projeto próprio e banco em `tmpfs`. A parada do stack de teste descarta sua base, sem tocar nos volumes demo ou real.

Os processos são agrupados localmente pelo número CNJ textual. Cada origem/tribunal continua como uma representação distinta. As versões de payload são imutáveis e identificadas por hash. Uma resposta de coleta cria observações locais vinculadas à consulta; a data de um movimento descreve o evento informado pela fonte, enquanto a data de observação registra quando o AgroJud recebeu o dado. Uma nova observação não prova que o tribunal praticou um novo ato.

Triagem e histórico humano ficam separados dos dados importados e não são substituídos por uma coleta. Acompanhamento registra inclusão e remoção; novidades e sinais apontam para evidência local e mantêm seu histórico. Estado do job e cobertura são dimensões diferentes: `concluído` informa que aquela consulta terminou sem rejeições conhecidas, não que toda a fonte ou o universo estadual esteja completo. Limite, rejeição, cursor inválido ou falha permanecem visíveis como cobertura parcial/erro.

As regras versionadas de sinais usam ocorrências locais normalizadas, não texto livre nem chamada HTTP. Só snapshots completos entram no processamento; evidência incompleta mantém o resultado anterior marcado como desatualizado. As regras atuais (penhora, leilão e recuperação judicial) são `synthetic_only`: o código TPU 11382, por exemplo, significa “Bloqueio, Penhora ou Arresto” e não prova vínculo rural, urgência ou conclusão jurídica. Sinais não alteram triagem humana. Novidade `NEW_OBSERVATION` descreve conteúdo observado pela primeira vez naquela representação, com data do evento e data de observação separadas; não declara novo ato judicial. Os códigos e as limitações de evidência estão no [catálogo TPU](../docs/evidence/catalogo-tematico-tpu-2026-10-09.json) e na [SPEC-014](../specs/SPEC-014-sinais-e-reprocessamento-local.md).

Indicadores descrevem a amostra persistida. Assuntos podem se sobrepor; processos e representações têm contagens distintas. CSV exporta o recorte local e marca `demo` ou `real`. Nenhum indicador mede risco jurídico, taxa de êxito, prazo processual ou total do contencioso do TJGO.

## Instalação demo

Não sobrescreva arquivos locais `.env.*`. Crie `.env.demo` uma vez a partir do exemplo e mantenha-o fora do Git:

```sh
if [ ! -f .env.demo ]; then cp .env.demo.example .env.demo; fi
docker compose --project-name agrojud-demo --env-file .env.demo -f compose.yaml up --detach --wait db
docker compose --project-name agrojud-demo --env-file .env.demo -f compose.yaml run --rm --no-deps api uv run --no-sync alembic upgrade head
docker compose --project-name agrojud-demo --env-file .env.demo -f compose.yaml --profile worker up --build --detach --wait api frontend worker
```

Abra `http://127.0.0.1:5173`. A API também publica `http://127.0.0.1:8000/api/v1`; o Nginx do frontend encaminha `/api/` sem publicar a rede interna. Para uma porta ocupada, altere `FRONTEND_PORT` ou `API_PORT` no arquivo local antes de iniciar.

O real usa `.env.real`, `agrojud-real`, frontend em `127.0.0.1:5174` e API em `127.0.0.1:8001`. O arquivo de exemplo não contém chave DataJud; a credencial local deve ser configurada somente no backend. O schema atual bloqueia coleta real enquanto S1/S2 não forem aprovados. Não execute migração ou probe real como parte da demonstração sintética.

## Migrations, jobs e desligamento

Subir PostgreSQL não aplica migrations. Rode `alembic upgrade head` explicitamente antes de esperar readiness ou iniciar worker. Uma readiness 503 com motivo de revisão pendente exige aplicar a migration compatível; não apague schema ou volume para contornar o erro.

O worker separado atende a fila persistida. A UI mostra tentativas, progresso confirmado, cobertura e falha; ações de cancelar, retomar e continuar são explícitas. Jobs equivalentes são deduplicados. Para acompanhar o ambiente:

```sh
docker compose --project-name agrojud-demo --env-file .env.demo -f compose.yaml --profile worker ps
docker compose --project-name agrojud-demo --env-file .env.demo -f compose.yaml logs --tail=100 api worker frontend db
curl --fail http://127.0.0.1:8000/api/v1/health/live
curl --fail http://127.0.0.1:8000/api/v1/health/ready
```

`docker compose stop` mantém os volumes. API, worker e PostgreSQL têm período de encerramento gracioso. Se um worker for encerrado durante um job, não há confirmação de sucesso no desligamento; o job mantém o último checkpoint confirmado e a lease expira. Após conferir ambiente e fonte selecionados, iniciar o worker permite a recuperação definida pelo lease. Uma consulta real não é iniciada só por restaurar um banco.

## Backup, restauração e reset

O backup usa `pg_dump` custom, grava arquivo e manifesto com permissão local `0600`, hash SHA-256, revisão e contagens/digests de processos, representações, versões, triagem, acompanhamento, novidades, sinais e jobs. O script só lê o banco de origem e recusa sobrescrever arquivos existentes. Guarde o arquivo fora do Git e controle seu acesso: ele contém dados persistidos, inclusive notas humanas.

```sh
./scripts/backup-db.sh demo
./scripts/backup-db.sh real
```

O comando imprime o caminho do `.dump` e do `.manifest`. A restauração de verificação exige ambos e o modo de origem; confira os dados antes de substituir o caminho de exemplo:

```sh
./scripts/restore-backup.sh demo ./backups/agrojud-demo-DATA.dump agrojud-restore-20261010
```

O restore usa um nome de projeto novo `agrojud-restore-*`, banco temporário em `tmpfs` e nenhuma API, interface ou worker em execução. Compara contagens e digests das entidades e decisões, valida SHA-256 e exige que a revisão Alembic restaurada corresponda exatamente ao código disponível. Revisão incompatível falha explicitamente; a rotina não migra o backup. Ao terminar, remove os containers/rede descartáveis, sem remover volumes dos projetos de origem. A configuração efetiva da fonte deve ser revisada antes de iniciar qualquer worker após uma restauração operacional.

Reset só está disponível para a demo. Primeiro pare os processos demo e depois informe o alvo e a confirmação explícitos:

```sh
docker compose --project-name agrojud-demo --env-file .env.demo -f compose.yaml --profile worker stop api frontend worker
./scripts/reset-demo.sh --target demo --confirm
```

O reset confere `AGROJUD_ENV=demo`, projeto `agrojud-demo` e banco `agrojud_demo`, recusa execução enquanto API/frontend/worker estiverem ativos e apaga somente o schema `public` desse banco. Rode as migrations explicitamente antes de reiniciar. Não há comando de reset para `real`.

## Testes e contrato frontend/API

Suíte backend serial em PostgreSQL isolado, lint, formatação e tipos:

```sh
if [ ! -f .env.test ]; then cp .env.test.example .env.test; fi
docker compose --project-name agrojud-test --env-file .env.test -f compose.test.yaml --profile test run --rm --build tests sh -lc 'uv run --no-sync ruff check src tests && uv run --no-sync ruff format --check src tests && uv run --no-sync mypy src && uv run --no-sync pytest'
```

Frontend:

```sh
cd frontend
npm ci
npm run lint
npm run typecheck
npm test
npm run build
npm run openapi:check
npm run openapi:generate
```

O fluxo Playwright inicia a API, o worker e o container Nginx em projeto `*e2e*`, migra explicitamente o PostgreSQL descartável e verifica navegação SPA, proxy `/api`, jobs, falhas, triagem, acompanhamento, novidades e CSV:

```sh
if [ ! -f .env.e2e ]; then cp .env.e2e.example .env.e2e; fi
./scripts/e2e.sh
```

Se as portas configuradas no `.env.e2e` estiverem ocupadas, use `E2E_API_PORT` e/ou `E2E_FRONTEND_PORT` para aquele comando, por exemplo `E2E_FRONTEND_PORT=18766 ./scripts/e2e.sh`.

`E2E_COMPOSE_PROJECT` pode selecionar outro nome que contenha `e2e`, mantendo o `.env.e2e` intacto. Use projeto novo quando quiser um banco descartável sem estado de uma execução anterior.

Para executar só a jornada integrada e a medição sintética, use `./scripts/e2e.sh e2e/operation.spec.ts`. O teste imprime uma linha `[SPEC-020-MEASUREMENT]` com versão do Docker/Compose/PostgreSQL/Node, máquina, fonte, resultados, páginas, retries, duração e tamanho do banco antes/depois da coleta. É uma amostra controlada de fixture local, não uma alegação de throughput DataJud.

### Medição registrada em 10/10/2026

Uma execução da jornada integrada em Compose recém-criado a partir de `.env.e2e.example`, com fonte `synthetic`, registrou: Linux x64, 12 CPUs, 15.647.834.112 bytes de memória total do host, Node v26.8.1, Docker Engine 29.8.2, Compose 5.6.0 e PostgreSQL 18.6; sem limites de CPU/memória configurados nos containers. A coleta obteve 1 resultado em 2 páginas, 0 retries, duração de 95 ms; o banco tinha 9.885.375 bytes antes e 10.409.663 bytes depois da coleta (variação observada de 524.288 bytes). A janela e a amostra são fixtures determinísticas, e esta única medição não sustenta alegação de throughput nem estimativa para DataJud/TJGO.

## Diagnóstico e limites conhecidos

- `frontend` não inicia: verifique `FRONTEND_PORT`, saúde da API e `docker compose logs frontend api`.
- API não fica pronta: confirme PostgreSQL saudável e aplique a migration explícita; não interprete readiness bloqueada como base vazia.
- Jobs ficam na fila: confirme que o serviço `worker` está ativo no perfil `worker`, veja logs e a fonte definida no arquivo do ambiente.
- Um erro DataJud permanece erro/estado inconclusivo. Não acione fixture no ambiente real nem apresente timeout, 429, 504 ou resposta inválida como zero resultados.
- S1/S2 seguem sem prova de filtro, busca exata, sort e duas páginas reais; S5 segue sem validação dos presets/payloads reais necessários. Os gates e respectivas evidências estão em [SPEC-003](../specs/SPEC-003-validacao-datajud-tjgo.md), [SPEC-010](../specs/SPEC-010-catalogo-tematico-versionado.md) e na matriz da [SPEC-020](../specs/SPEC-020-operacao-e-aceite-integrado.md).
- O projeto é local, de um operador e sem login. Não exponha portas à rede, não use como serviço profissional/comercial e não publique dados derivados sem avaliação dos termos vigentes e da finalidade.
