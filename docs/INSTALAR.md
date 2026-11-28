# Hospedar o minerva-seerr

Guia para quem quer rodar o seu. São uns 15 minutos. Você só precisa de um computador ligado em casa com Docker
e de um qBittorrent.

## O que você precisa

| Item | Para quê | Obrigatório? |
|---|---|---|
| Linux com **Docker** e **Docker Compose** (ou Docker Desktop) | roda o app | sim |
| **qBittorrent 4.4+** com o Web UI ligado (o 5.x é o recomendado) | baixa os arquivos | sim |
| Espaço em disco para os jogos (no qBittorrent) | download | sim |
| ~100 MB para o app (catálogo, capas) | banco e cache | sim |
| Conta na Twitch (app de desenvolvedor) | fichas e capas do IGDB | não |
| PCs Windows com OpenSSH + o worker do [pedidos-torrent](https://github.com/feartsOFC/pedidos-torrent) | entrega automática no PC de quem pediu | não |

Sem o worker de entrega, o app funciona como "pedido → download no qBittorrent", e você pega os arquivos na pasta
do qBit.

> Sobre o conteúdo: o app só organiza o índice público do Minerva Archive e manda o qBittorrent baixar os
> arquivos escolhidos. Respeite as leis do seu país e baixe só o que você tem direito de ter.

## 1. Baixar e configurar

```bash
git clone <este repositório> minerva-seerr && cd minerva-seerr
cp .env.example .env && chmod 600 .env
nano .env        # ajuste pelo menos MINERVA_URL e QBIT_URL
```

- `MINERVA_URL`: o endereço que você vai abrir no navegador (ex.: `http://192.168.1.50:8000`).
- `QBIT_URL`: o Web UI do qBittorrent (ex.: `http://192.168.1.50:8080`).

## 2. Preparar o qBittorrent

No qBittorrent: **Ferramentas → Opções → Web UI**:
- ligue o Web UI;
- em "Ignorar autenticação para clientes nos IPs/sub-redes da lista", ponha o IP da máquina do app (ou a sua rede,
  ex.: `192.168.1.0/24`). Se preferir não fazer isso, preencha `QBIT_USUARIO` e `QBIT_SENHA` no `.env`.

Recomendado em **Downloads**: "Manter arquivos incompletos em" uma pasta separada, e em **BitTorrent** o limite de
proporção 0 com a ação "Parar torrent" (o app não precisa semear).

O app cria sozinho a categoria `minerva`. Cada coleção entra como **um** torrent parado, com todos os arquivos
desligados, e o app liga só os que foram pedidos.

## 3. Subir

```bash
docker compose up -d --build
docker compose exec app python -m unittest discover -s tests -t .    # opcional: 67 testes, ~10 s
docker compose exec app python -m app.indexer atualizar               # baixa o índice (~40 MB, 1 min)
```

## 4. Criar o administrador

```bash
docker compose exec app python -m app.configurar
# → http://192.168.1.50:8000/c/abcd-efgh-jkmn
```

Abra o link no navegador. Numa instalação nova ele pede o **nome e a senha do primeiro admin** (e, se quiser, as
chaves do IGDB). O link vale uma vez só e expira em 60 minutos.

Alternativa pelo terminal: `docker compose exec app python -m app.usuarios criar ana --admin --gerar` (mostra uma
senha aleatória uma vez).

Pronto: entre em `MINERVA_URL/login`.

## 5. Ajustes na interface (admin)

Em **Ajustes**:
- **Pedidos e limites**: GB por pedido (as DLCs contam), pedidos por dia, quantos pedidos baixam ao mesmo tempo
  (padrão 1; 0 = sem limite), espaço mínimo livre no qBit, pasta no PC
  (`Jogos/{plataforma}`), aprovação do admin para usuários comuns;
- **qBittorrent**: botão "Testar conexão" (mostra a versão e o espaço livre);
- **Capas e fichas**: libretro-thumbnails (sem chave) e IGDB (Client ID/Secret);
- **Indexador**: atualizar o catálogo;
- **Usuários**: quem existe e qual PC recebe.

Outros usuários: `docker compose exec app python -m app.usuarios criar beto --pc beto --gerar`.

## 6. Capas e fichas do IGDB (opcional)

1. Em https://dev.twitch.tv/console → **Registrar aplicativo**: categoria qualquer, tipo **Confidencial**,
   redirect `http://localhost`.
2. Copie o **Client ID** e gere um **Client Secret**. O segredo aparece uma vez só.
3. Cole em **Ajustes → Capas e fichas** (ou no link de configuração) e salve. A tela confirma se a Twitch aceitou.
4. Para preencher uma plataforma inteira de uma vez (o Xbox 360 leva ~12 min):
   `docker compose exec -d app sh -c 'python -m app.meta.capas X360 > /data/lote.log 2>&1'`

Sem o IGDB, as capas vêm do libretro-thumbnails (N64 ~95%, SNES ~93%, PS2 ~72%; Xbox 360 quase nada).

## 7. HTTPS com proxy reverso (opcional)

Exemplo com Caddy:

```
minerva.minha.casa {
    reverse_proxy 192.168.1.50:8000
}
```

No `.env`: `MINERVA_URL=https://minerva.minha.casa` e `MINERVA_PROXY_IPS=<IP do Caddy>` (assim o app sabe que
o acesso é por HTTPS e marca o cookie de sessão como `Secure`). Depois: `docker compose up -d`.

**Não exponha o app na internet.** Ele foi feito para a rede de casa. Para acessar de fora, use uma VPN
(Tailscale, WireGuard).

## 8. Entrega automática no PC (opcional)

O `worker/minerva_entrega.py` roda no host (não no container), a cada 5 minutos. Ele:
1. pergunta ao app o que já baixou;
2. liga o PC do dono por Wake-on-LAN, se estiver desligado;
3. copia só os arquivos do pedido por SSH (`tar` → `tar.exe` do Windows) e confere os tamanhos;
4. avisa o app (que tira o arquivo do torrent ou apaga o torrent) e apaga do disco do servidor;
5. desliga o PC se foi ele que ligou, e avisa no Discord.

Ele reaproveita o worker do projeto **pedidos-torrent** como biblioteca: a lista de PCs (IP, MAC, usuário SSH,
pasta de destino), as faixas de horário bloqueadas, o WoL e o aviso no Discord vêm de lá. Instale aquele primeiro
e siga o README dele para os PCs (OpenSSH Server no Windows, chave SSH, WoL na BIOS).

```bash
sudo install -m 755 worker/minerva_entrega.py /usr/local/bin/minerva-entrega.py
sudo mkdir -p /etc/minerva-seerr && sudo cp worker/worker.example.json /etc/minerva-seerr/worker.json
sudo chmod 600 /etc/minerva-seerr/worker.json && sudo nano /etc/minerva-seerr/worker.json   # token = MINERVA_WORKER_TOKEN
sudo /usr/local/bin/minerva-entrega.py --config /etc/minerva-seerr/worker.json --teste       # só mostra o que faria
echo '*/5 * * * * root flock -w 240 /run/pedidos-torrent.lock /usr/local/bin/minerva-entrega.py --config /etc/minerva-seerr/worker.json >/dev/null 2>>/var/log/minerva-entrega.log' | sudo tee /etc/cron.d/minerva-entrega
```

O cron acima usa **o mesmo arquivo de lock** do pedidos-torrent (`/run/pedidos-torrent.lock`), para os dois nunca
ligarem ou desligarem o mesmo PC ao mesmo tempo. Em `worker.json`, `base` aponta para o worker instalado do
pedidos-torrent (`/usr/local/bin/pedidos-torrent-worker`).

Ao criar os usuários, use em `--pc` a mesma chave do PC no `config.json` do pedidos-torrent.

No PC: o jogo vai para `<pasta de torrents>\Jogos\<plataforma>`, as DLCs para `...\DLC\<jogo>` e as atualizações
para `...\Updates\<jogo>`.

## 9. Atualizar o app

```bash
git pull && docker compose up -d --build
```

O banco (`data/minerva.db`) se ajusta sozinho às versões novas.

## Backup

Basta copiar a pasta `data/` com o app parado: `minerva.db`, `torrents/` (pode ser baixada de novo) e `capas/`
(pode ser refeita).

## Problemas comuns

| Sintoma | Causa provável |
|---|---|
| "Testar conexão" do qBit falha | Web UI desligado, IP fora da whitelist ou usuário/senha faltando no `.env` |
| Pedido fica em "esperando a vez" | já há o máximo de pedidos baixando (Ajustes → "baixando ao mesmo tempo"); começa sozinho quando o atual terminar |
| Pedido fica em "sem espaço" | o qBit tem menos livre que o "espaço mínimo"; começa sozinho quando liberar |
| Login volta para a tela de login | navegador bloqueando cookies do endereço; teste em outra aba anônima |
| Atrás do proxy, o cookie não sai `Secure` | `MINERVA_PROXY_IPS` não tem o IP do proxy |
| Link de configuração aponta para `localhost` | `MINERVA_URL` não preenchido no `.env` (recrie com `docker compose up -d`) |
| IGDB "invalid client secret" | o segredo foi colado errado; gere outro no console da Twitch |
| Cópia para o PC falha com `PathNotFound` | worker antigo; os atuais criam a pasta com `[IO.Directory]::CreateDirectory` (PowerShell 5.1) |
