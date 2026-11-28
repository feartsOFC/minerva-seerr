# minerva-seerr — operação

Como este homelab opera o app. Para instalar do zero em outro lugar, veja [INSTALAR.md](INSTALAR.md).

Todos os comandos rodam no **host Proxmox**. `D` = `pct exec 210 -- docker exec minerva-seerr`.

## Saúde

```bash
curl -s http://10.0.77.41:8000/api/saude          # {"ok": true, colecoes, arquivos, ...}
pct exec 210 -- docker ps --format '{{.Names}} {{.Status}}'
pct exec 210 -- docker logs --tail 50 minerva-seerr
tail -n 30 /var/log/homelab-avisos.log | grep minerva-entrega   # worker de entrega
```

Monitores: Uptime Kuma `CT210 - Minerva Web` (`/api/saude`, alerta no Discord #status) e o item "Minerva" no
painel `homepage.home` (grupo Mídia).

## Usuários e senhas

- Trocar a própria senha: **Ajustes → Minha senha** (na interface).
- Sem terminal com colar? Gere um **link de configuração de uso único** (vale 1 vez, expira sozinho). Ele abre um
  formulário para as chaves do IGDB e as senhas de cada usuário:
  ```bash
  pct exec 210 -- docker exec minerva-seerr python -m app.configurar        # 60 min; "… configurar 15" = 15 min
  # → https://minerva.home/c/abcd-efgh-jkmn   (sem 0/O/1/l/I, dá para digitar; endereço vem de MINERVA_URL)
  ```
- Pelo terminal (precisa de TTY de verdade, não funciona pelo `!` do Claude Code):
  `pct exec 210 -- docker exec -it minerva-seerr python -m app.usuarios senha <usuário>`
- Criar usuário: `... python -m app.usuarios criar <nome> [--admin] --pc <chave do PC no /etc/pedidos-torrent/config.json> --gerar`

Trocar a senha derruba as outras sessões do usuário.

## Índice e catálogo

```bash
pct exec 210 -- docker exec minerva-seerr python -m app.indexer atualizar     # só baixa o que mudou (304)
pct exec 210 -- docker exec minerva-seerr python -m app.indexer stats
```

Também pelo botão **Ajustes → Indexador** (só o admin). Coleção nova: `MINERVA_COLECOES` no `.env` (ou acrescentar
em `app/config.py` → `MVP`), recriar o container e atualizar.

Religar as DLCs/updates aos jogos (por exemplo, depois de mudar a regra de casamento):

```bash
pct exec 210 -- docker exec minerva-seerr python -c "
from app import config; from app.indexer import store; from app.catalog import catalogo
c=store.conectar(config.BANCO); (p,)=c.execute(\"select id from plataformas where curto='X360'\").fetchone()
catalogo.ligar_complementos(c,p); c.commit()"
```

## Capas e IGDB

- Chaves do IGDB: app em dev.twitch.tv (tipo Confidencial, redirect `http://localhost`). Gravadas no banco do app
  (Ajustes → Capas e fichas, ou link de configuração). O `.env` só serve de padrão.
- O "Client Secret" da Twitch aparece uma vez só. Se errar, gere um novo no console da Twitch.
- Lote de fichas de uma plataforma (respeita o limite de 4 req/s; o Xbox 360 leva ~12 min):
  `pct exec 210 -- docker exec -d minerva-seerr sh -c 'python -m app.meta.capas X360 > /data/lote.log 2>&1'`
  **Não recriar o container enquanto o lote roda** (o processo morre junto).
- Capas do libretro: `POST /api/capas/casar` (admin) ou o botão em Ajustes. As capas se baixam sozinhas quando a
  página do jogo é aberta. Falhas só são tentadas de novo depois de 7 dias.

## Pedidos travados

| Status | O que fazer |
|---|---|
| `aguardando_vez` | normal: só 1 pedido baixa por vez (Ajustes → "baixando ao mesmo tempo"); anda sozinho quando o atual vira `baixado` ou é cancelado. Se o atual ficar `pausado` para sempre, a fila também para |
| `sem_espaco` | espera sozinho; libera espaço no `/data` do CT206 ou baixa "espaço mínimo" em Ajustes |
| `erro` "torrent removido" | alguém apagou no qBit: botão **Repetir** em Solicitações |
| `baixado` com mensagem "vai para o PC depois do…" | faixa bloqueada (ponta 17:30–20:30 em dias úteis, backup 02:15–04:30); normal |
| `baixado` "cópia falhou" | o worker tenta a cada 5 min e avisa uma vez só; ver `/var/log/homelab-avisos.log` |
| `entregando` parado > 2 h | o worker pega de novo sozinho |

Pausar as entregas: `mv /etc/cron.d/homelab-minerva-entrega{,.pausado}` (e o contrário para religar).

## Arquivos no servidor

O qBit do CT206 salva em `/data/torrents/minerva` e os incompletos em `/data/torrents/incompletos/minerva`.
Quando só uma parte do torrent foi pedida, o arquivo fica nos **incompletos** (o qBit não move); o worker procura
nas duas pastas. Depois da entrega, o app tira o arquivo do torrent (prioridade 0) e o worker apaga do disco;
se nenhum pedido usa mais aquele torrent, o app apaga o torrent inteiro com os arquivos.

## Testes

```bash
cd /root/projetos/minerva-seerr && python3 -m unittest discover -s tests -t .
pct exec 210 -- bash -c "cd /opt/minerva-seerr && docker compose run --rm --no-deps -e MINERVA_SEM_LACO=1 app python -m unittest discover -s tests -t ."
```

## Regras do homelab que valem aqui

- Editar arquivo dentro do CT: diff + `.bak` antes; apagar o `.bak` depois de testado.
- Não parar, reiniciar nem recriar containers de outros CTs sem pedir.
- Não ler o `.env`; não usar `docker compose config` nem `docker inspect` sem `--format`.
- Toda mudança → `/root/mudancas-homelab.md`, mapa (`/root/mapa-homelab.html` → mapa.home) se mudar rede/porta/nome,
  e `/opt/homelab-memory/services/ct210-minerva-seerr.md`.
