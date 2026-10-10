# Sistema visual do AgroJud Radar

## Princípios

- **Produto de trabalho:** mostrar o estado e as ações em ordem de uso, sem linguagem de campanha ou decoração que dispute com dados jurídicos.
- **Leitura primeiro:** rótulos curtos, números tabulares, informação completa em detalhe e layouts que tolerem nomes de origem longos.
- **Evidência preservada:** sinalizar origem, datas distintas, estado de revisão e multiplicidade sem transformar observação técnica em conclusão jurídica.
- **Reutilização:** compor os wrappers locais de Radix e os componentes compartilhados existentes; manter a camada de API tipada e inalterada.

## Tipografia

- IBM Plex Sans local para interface, pesos 400, 500 e 600.
- IBM Plex Mono local, peso 400, para números CNJ, identificadores e dados tabulares.
- Corpo 14 px com entrelinha 1,5; títulos usam a escala e os pesos do Tailwind; números de destaque usam algarismos tabulares.
- Arquivos WOFF2 em `public/fonts/`; licença IBM incluída em `public/fonts/OFL.txt`.

## Cores e usos

| Token CSS | Valor | Uso |
| --- | --- | --- |
| `--background` | `#f5f7f6` | Fundo geral |
| `--card` | `#ffffff` | Superfícies de conteúdo |
| `--foreground` | `#202925` | Texto principal |
| `--muted-foreground` | `#58665f` | Rótulos e metadados |
| `--primary` | `#205b49` | Ação principal, seleção e identidade |
| `--accent` | `#e4eee8` | Navegação e evidência em foco |
| `--border` | `#dbe2dd` | Divisores e limites de controle |
| `--info` / `--info-foreground` | `#eaf1f9` / `#285479` | Estado informativo |
| `--warning` / `--warning-foreground` | `#fff3d9` / `#785012` | Atenção, parcialidade e ambiente demo |
| `--success` / `--success-foreground` | `#e4f1e9` / `#205b49` | Conclusão positiva |
| `--destructive` / `--destructive-foreground` | `#a32932` / `#ffffff` | Falha e remoção |

Reservar vermelho para falha/remoção e âmbar para atenção; não usar cor sozinha como rótulo de estado. Limites, texto e nomes acessíveis acompanham a cor. O foco usa o verde primário e é visível por teclado.

Os pares de texto dos tokens acima foram conferidos por luminância relativa: corpo 13,89:1; texto secundário 5,61:1 no fundo geral e 6,03:1 no cartão; texto branco sobre ação primária 7,91:1; texto informativo 7,00:1; alerta 6,46:1; sucesso 6,80:1; texto destrutivo 7,19:1. A borda de controle (`--input`) alcança 3,04:1 sobre cartão.

## Navegação e layout

- Sidebar de 232 px a partir de 1280 px; versão compacta de 72 px entre 1024 e 1279 px; drawer Radix em larguras inferiores.
- Conteúdo central usa até 1600 px, gutters de 32 px em desktop e 16 px em dispositivos menores.
- Detalhe de processo combina conteúdo principal e revisão em coluna de 340 px em desktop; em larguras menores, revisão vira painel expansível.
- Em telas de até 767 px, tabelas de processos e registros acompanhados se tornam linhas empilhadas; os cabeçalhos permanecem disponíveis a leitores de tela.
- Alvos interativos têm ao menos 44 px em dispositivos com ponteiro de toque; reduzir animação quando `prefers-reduced-motion` está ativo.

## Componentes e estados

- Cards planos, raio de 8 px e borda neutra; evitar empilhar cards para agrupar uma única informação.
- Badges compactos e com texto; usar tokens semânticos para decisão, estado de job e novidade.
- Filtros ativos aparecem como chips removíveis com ação nomeada; filtros avançados não ocupam a área inicial até serem pedidos.
- Abas preservam formulários montados ao alternar painel quando há rascunho local.
- Ações destrutivas usam `ConfirmAction`; edição de triagem protege navegação e saída da página.
- Feedback: skeleton específico da área no carregamento; vazio explica ausência; erro oferece retry quando disponível; stale e parcial continuam explícitos.
- Linha do tempo mantém datas do evento, da atualização na fonte e da observação local separadas; evidência selecionada recebe âncora e foco.

## Breakpoints de validação

| Largura | Composição esperada |
| ---: | --- |
| 390 px | Drawer, filtros e formulário empilhados, tabela de processos em registros rotulados, sem rolagem horizontal |
| 640 px | Equivalente aproximado a desktop de 1280 px com zoom de 200%; drawer e conteúdo sem overflow |
| 768 px | Drawer e painéis em uma coluna, filtros em até duas colunas |
| 1024 px | Sidebar compacta e área útil sem corte |
| 1440 px | Sidebar completa, detalhe com revisão lateral e tabelas densas |

Ver [SPEC-021](../specs/SPEC-021-redesign-frontend.md) para aceite de produto e validação.
