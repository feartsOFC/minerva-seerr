# Arquitetura

```
 navegador ──(https)──▶ proxy reverso (opcional) ──▶ minerva-seerr :8000  (FastAPI + SQLite, um container)
                                                       │  índice (.torrent das coleções) ◀── cdn.minerva-archive.org
                                                       │  capas e fichas ◀── libretro-thumbnails (GitHub) · IGDB (Twitch)
                                                       │
                                                       ├─▶ qBittorrent (Web API v2): torrent da coleção entra PARADO,
                                                       │   prioridade 0 em tudo, liga só os arquivos pedidos e inicia
                                                       │
 worker de entrega no host (cron 5 min, opcional) ◀── /api/worker/entregas (token)
   └─ WoL no PC do dono → cópia por SSH (tar → tar.exe) → confere tamanhos → avisa o app
      → app tira o arquivo do torrent (ou apaga o torrent) → aviso no Discord
```

Um processo só (uvicorn), um banco só (`data/minerva.db`), sem fila e sem serviço extra. Um laço em segundo plano
sincroniza o status com o qBittorrent a cada 20 s.

## Módulos

```
app/
  main.py            API JSON, laço de status, API do worker, link de configuração de uso único
  configurar.py      CLI: gera o link de configuração
  config.py          caminhos (MINERVA_DADOS), URL do índice, coleções padrão
  indexer/           bencode, torrent/infohash, magnet, banco (SQLite + FTS5 trigram), atualização, CLI
  catalog/           análise de nomes No-Intro/Redump (titles.py) e catálogo por jogo/versão/complemento
  meta/              libretro, IGDB, capas (lote: python -m app.meta.capas X360)
  pedidos.py         limites, envio ao qBit, sincronização, liberação, API do worker
  qbit.py            cliente da Web API v2 do qBittorrent
  usuarios.py        usuários, senhas (PBKDF2), sessões; CLI
  web/               páginas (Jinja + HTMX + Alpine locais) e CSS
worker/minerva_entrega.py   entrega no PC (roda no host, fora do container)
tests/                      unittest (os de web/API pulam sem FastAPI instalado)
```

## 1. Indexador

- Lê a listagem plana do CDN (`/` lista os `.torrent`), filtra pelas coleções escolhidas e baixa com
  `If-Modified-Since`. Coleção sem mudança volta 304 e não é baixada de novo.
- Decodifica o bencode guardando os bytes exatos do dicionário `info`, e o infohash v1 é o SHA-1 desses bytes.
- **`so=` (BEP 53)** é o índice do arquivo em `info.files`, **contando** os arquivos de enchimento (padding,
  BEP 47: `attr` com `p`, `.pad/…`, `_____padding_file…`). Eles saem da lista visível, mas a numeração continua
  a mesma.
- O qBittorrent **esconde** os paddings e põe a pasta-raiz no nome. Por isso a numeração dele é outra, e o app
  acha cada arquivo no qBit **pelo caminho**, nunca pelo número do `so=`.
- Busca: tabela FTS5 com tokenizer `trigram` sobre o nome dos arquivos (acha "guitar hero" e "hero III").

## 2. Catálogo

- `titles.analisar(nome)` separa título, região, idiomas, revisão (`Rev 1`, `v1.02`), disco (`Disc 2`,
  `Disco 2`, `Disque 2`, partes nomeadas como `Game Disc` e `Install Disc`) e tipo (jogo/demo/beta/bônus). O
  artigo final vai para a frente ("Legend of Zelda, The" → "The Legend of Zelda").
- Um **jogo** = plataforma + chave do título; uma **versão** = região + idiomas + revisão + tipo + extras; os
  discos da mesma versão andam juntos.
- A versão principal é escolhida por região (Brasil, EUA, Mundo, Europa, Japão) e pela revisão mais nova.
- IDs estáveis (`UNIQUE(plataforma_id, chave)`): reindexar não muda os links nem os pedidos.

## 3. Complementos (DLC e title updates)

As coleções `… (Digital)` e `… (Title Updates)` não criam jogos. Cada arquivo `(DLC)`/`(Addon)` ou `(vN)` é preso
ao jogo-base da mesma plataforma:
1. Prefixo mais longo antes de ` - ` igual ao título de um jogo ("Guitar Hero III - Legends of Rock - Muse Track
   Pack" → Guitar Hero III; rótulo "Muse Track Pack").
2. Algarismos romanos valem o mesmo que arábicos ("Assassin's Creed 2" → "Assassin's Creed II").
3. Sem nome igual, vale o **único** jogo cujo título começa pelo nome ("Minecraft" → "Minecraft - Xbox 360
   Edition"). Com dois candidatos, a DLC não é ligada, para não cair no jogo errado.

Arquivos sem jogo-base (jogos XBLA, DLC de jogo fora do catálogo) viram `avulso`. O vínculo é refeito sempre que
uma coleção muda.

## 4. Capas e fichas

- **libretro-thumbnails**: lista os arquivos do repositório da plataforma pela API do GitHub (cache de 7 dias),
  casa o nome com `difflib`. Uma trava de números impede pegar a continuação errada (Halo 2 ≠ Halo 3); casamento
  aproximado fica marcado como "incerto".
- **IGDB**: token client-credentials da Twitch, limite de 4 req/s, busca pelo título dentro da plataforma.
- As imagens são reduzidas para JPEG de 480 px (~40–90 KB) e ficam em `data/capas`. Falhas só são tentadas de
  novo depois de 7 dias. Sem capa, o app desenha um SVG no estilo da interface.

## 5. Pedidos

1. Confere: a versão e os complementos pertencem ao jogo; o tamanho total (DLCs incluídas) cabe no limite; a
   cota diária (o admin não tem); e não há pedido ativo repetido.
2. Status inicial `aguardando_vez`, ou `aguardando_aprovacao` se a aprovação estiver ligada e quem pediu não for admin.
   **Fila**: só vai para o qBit se houver vaga — no máximo `baixando_max` pedidos (padrão 1; 0 = sem limite) em
   `na_fila`/`baixando`/`pausado`. Sem vaga, fica em `aguardando_vez`; a sincronização manda o próximo, por ordem
   de pedido, quando um vira `baixado` ou é cancelado. Um pedido em `sem_espaco` não ocupa vaga.
3. **Envio ao qBit**: se o torrent da coleção ainda não existe, ele entra **parado**, com todos os arquivos em
   prioridade 0. Depois o app liga só os do pedido e inicia. Se já existe (outro pedido da mesma coleção), só
   liga os arquivos novos.
4. Se o espaço livre no qBit menos o que falta baixar dos pedidos ativos ficar abaixo do mínimo → `sem_espaco`.
   O pedido começa sozinho quando liberar.
5. **Sincronização** (20 s): progresso por arquivo e velocidade; `baixado` quando todos os arquivos chegam a 100%.
   A cada 5 min, **conferência**: nos torrents dos pedidos em andamento, arquivo ligado que nenhum pedido usa é
   desligado ("carona" que divide pedaço com o arquivo pedido) e arquivo de pedido baixando que ficou desligado e
   incompleto é religado. Tudo vai para o log (`conferência …`); nada é apagado do disco.
6. **Liberação** (entregue ou cancelado): se nenhum outro pedido ativo usa o torrent, apaga o torrent com os
   arquivos. Se não, só põe os arquivos deste pedido em prioridade 0 e devolve a lista para o worker apagar do
   disco.

Status: `aguardando_aprovacao → aguardando_vez → na_fila → baixando → baixado → entregando → entregue` (mais `sem_espaco`,
`pausado`, `erro`, `cancelado`).

## 6. Entrega (worker no host)

- `GET /api/worker/entregas` (header `X-Minerva-Token`) lista os pedidos `baixado` e os `entregando` parados há
  mais de 2 h.
- Usa o worker do projeto **pedidos-torrent** como biblioteca: PCs (IP, MAC, usuário SSH, pastas), faixas
  bloqueadas (por exemplo, horário de ponta da tarifa e janela de backup), WoL, SSH e aviso no Discord. Também
  usa o mesmo lock no cron.
- Acha o arquivo na pasta final ou na de incompletos do qBit (quando só parte do torrent é pedida, o qBit não
  move o arquivo). Copia com `tar --format=pax` → `ssh` → `tar.exe -x` no Windows e confere o tamanho de cada
  arquivo. Tenta até 3 vezes. A leitura do `tar` passa pelo `leitor_tar` do worker base: no host do homelab ele
  pausa os torrents durante a cópia e limita a leitura se o disco saturar (modo cópia); fora dele, só `nice`.
- Pastas no PC: `Jogos\<plataforma>`, `…\DLC\<jogo>`, `…\Updates\<jogo>` (nomes sem acento nem caracteres
  proibidos no Windows). A pasta é criada com `[IO.Directory]::CreateDirectory`, porque o `New-Item` do
  PowerShell 5.1 não aceita `-LiteralPath`.
- Se não consegue copiar, volta o pedido para `baixado` com a mensagem e avisa **uma vez**. Desliga o PC só se
  foi ele que ligou.

## Banco (SQLite)

| Tabela | Conteúdo |
|---|---|
| `colecoes`, `arquivos`, `busca` (FTS5) | índice: coleção, infohash, trackers, arquivos com `idx` (= `so=`), catálogo por arquivo |
| `plataformas`, `jogos`, `metadados` | catálogo agrupado, capas, fichas do IGDB |
| `usuarios`, `sessoes` | contas e sessões (30 dias) |
| `pedidos`, `pedido_arquivos` | pedidos e arquivos de cada pedido (`subpasta` para DLC/updates) |
| `ajustes` | configuração da tela de Ajustes, chaves do IGDB, token do link de configuração (só o hash) |

As tabelas e colunas novas são criadas sozinhas ao abrir o banco (`CREATE … IF NOT EXISTS` e `ALTER TABLE ADD
COLUMN`).

## API (resumo)

| Rota | Quem |
|---|---|
| `GET /api/saude`, `/api/busca`, `/api/jogos`, `/api/jogos/{id}`, `/api/plataformas`, `/capas/{id}` | qualquer um na rede |
| `POST /api/login`, `/api/logout`, `GET /api/eu` | sessão |
| `POST /api/pedidos`, `GET /api/pedidos`, `POST /api/pedidos/{id}/cancelar` | usuário logado |
| `POST /api/pedidos/{id}/aprovar`, `/api/indexador/atualizar`, `/api/capas/*`, `GET /api/qbit/teste` | admin |
| `GET/POST /api/worker/entregas…` | token do worker |
| `/c/{código}`, `/configurar` | link de uso único |
