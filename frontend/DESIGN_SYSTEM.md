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
| `--background` | `#F7F8F5` | Fundo geral |
| `--surface` / `--card` | `#FFFFFF` | Superfícies de conteúdo e menus |
| `--text-primary` / `--foreground` | `#18211B` | Texto principal |
| `--text-secondary` / `--muted-foreground` | `#667069` | Rótulos e metadados |
| `--primary` | `#1F5D42` | Ações principais e identidade |
| `--primary-dark` | `#163C2D` | Hover, seleção e texto sobre verde suave |
| `--primary-soft` | `#EAF3EE` | Seleção, chips e hover de controles |
| `--accent` | `#B68A3A` | Indicadores gráficos discretos e acento da marca |
| `--border` | `#DDE2DD` | Divisores e limites de superfícies |
| `--input` | `#75827A` | Bordas de campos e controles interativos com contraste reforçado |
| `--muted` | `#F1F3F0` | Cabeçalhos de tabela e superfícies neutras secundárias |
| `--info` / `--info-foreground` | `#EAF1F9` / `#285479` | Estado informativo |
| `--warning` / `--warning-foreground` | `#F6F0E5` / `#5B461F` | Atenção, parcialidade e ambiente demo |
| `--success` / `--success-foreground` | `#EAF3EE` / `#163C2D` | Conclusão positiva |
| `--destructive` / `--destructive-foreground` | `#A32932` / `#FFFFFF` | Falha e remoção |
| `--overlay` / `--shadow-color` | `rgb(24 33 27 / 40%)` / `rgb(24 33 27 / 15%)` | Modal e sombra ligados ao texto principal |

Verde escuro identifica ações e seleção; o verde suave identifica superfícies de interação; o dourado aparece em marcadores de timeline e realces curtos. Reservar vermelho para falha/remoção e fundo dourado claro para atenção; não usar cor sozinha como rótulo de estado. O dourado não é usado como texto sobre fundo claro. Limites, texto e nomes acessíveis acompanham a cor. O foco usa o verde primário e é visível por teclado.

Pares foram conferidos por luminância relativa: texto principal no fundo geral 15,49:1; texto secundário no fundo geral 4,82:1 e na superfície 5,14:1; branco sobre ação primária 7,77:1; texto primário sobre o dourado 5,26:1; texto de alerta 7,91:1; sucesso 10,80:1; texto informativo 7,00:1; texto destrutivo 7,19:1. O acento dourado sobre superfície branca alcança 3,14:1 e é restrito a indicadores gráficos. A borda interativa (`--input`) alcança 4,01:1 sobre superfície.

`src/theme.test.ts` verifica os valores da marca e os pares de contraste para texto e elementos gráficos. O E2E verifica os estilos computados no fundo, navegação ativa, ação principal, badge de sucesso, filtro, cabeçalho de tabela, superfície do detalhe e marcador da timeline.

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
