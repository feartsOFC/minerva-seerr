"""Pedidos: limites, envio ao qBittorrent (só o arquivo pedido), status e entrega.

Fluxo de status:
  aguardando_aprovacao → aguardando_vez → na_fila → baixando → baixado → entregando → entregue
                                       ↘ sem_espaco (volta sozinho quando liberar)  ↘ erro / cancelado
aguardando_vez: ainda não foi para o qBit porque já há `baixando_max` pedidos baixando (padrão 1; 0 = sem limite).
A fila anda por ordem de pedido na sincronização (a cada 20 s), quando um pedido termina de baixar ou sai.
Quem faz a entrega no PC é o worker do host (WoL, cópia, conferência); o app só diz o que está pronto
e, quando o worker confirma, tira o arquivo do torrent (e o torrent inteiro, se ninguém mais usa).
"""
import json
import logging
import os
import re
import time
import unicodedata

from .catalog import catalogo
from .indexer import store
from .indexer.magnet import montar_magnet
from .qbit import ErroQbit, Qbit, indice_por_caminho

log = logging.getLogger("minerva.pedidos")
GB = 1024 ** 3

ESQUEMA = """
CREATE TABLE IF NOT EXISTS pedidos (
    id            INTEGER PRIMARY KEY,
    usuario_id    INTEGER NOT NULL REFERENCES usuarios(id),
    jogo_id       INTEGER NOT NULL,
    titulo        TEXT NOT NULL,
    plataforma    TEXT NOT NULL,          -- nome curto ("Xbox 360")
    sistema       TEXT NOT NULL,          -- pasta: "Microsoft - Xbox 360"
    versao_chave  TEXT NOT NULL,
    versao_rotulo TEXT NOT NULL,
    tamanho       INTEGER NOT NULL,
    status        TEXT NOT NULL,
    progresso     REAL DEFAULT 0,
    velocidade    INTEGER DEFAULT 0,
    erro          TEXT,
    magnet        TEXT,                   -- magnet do 1º arquivo (registro do so= enviado)
    destino       TEXT,                   -- onde ficou no PC (preenchido pelo worker)
    criado_em     REAL,
    atualizado_em REAL,
    baixado_em    REAL,
    entregue_em   REAL,
    aprovado_por  INTEGER
);
CREATE TABLE IF NOT EXISTS pedido_arquivos (
    pedido_id  INTEGER NOT NULL REFERENCES pedidos(id) ON DELETE CASCADE,
    colecao_id INTEGER NOT NULL,
    infohash   TEXT NOT NULL,
    so         INTEGER NOT NULL,          -- índice em info.files (o do magnet)
    caminho    TEXT NOT NULL,             -- caminho dentro do torrent
    tamanho    INTEGER NOT NULL,
    qbit_index INTEGER,                   -- índice que o qBit usa no filePrio
    qbit_nome  TEXT,                      -- nome no qBit (com a pasta-raiz)
    progresso  REAL DEFAULT 0,
    PRIMARY KEY (pedido_id, infohash, so)
);
CREATE INDEX IF NOT EXISTS ix_ped_status ON pedidos(status);
CREATE INDEX IF NOT EXISTS ix_pa_hash ON pedido_arquivos(infohash);
"""
ATIVOS = ("aguardando_aprovacao", "aguardando_vez", "sem_espaco", "na_fila", "baixando", "pausado", "baixado", "entregando")
NO_QBIT = ("na_fila", "baixando", "pausado", "baixado", "entregando")
EM_DOWNLOAD = ("na_fila", "baixando", "pausado")   # ocupam vaga de download (baixado já libera a vaga)
ESPERANDO_VAGA = ("aguardando_vez", "sem_espaco")
MSG_VEZ = "esperando a vez — começa quando o download atual terminar"
CONFERIR_A_CADA = 300   # s entre conferências dos arquivos ligados no qBit (ver conferir)
_ultima_conferencia = 0.0
PADROES = {
    "qbit_url": os.environ.get("QBIT_URL", "http://localhost:8080"),
    "qbit_usuario": os.environ.get("QBIT_USUARIO", ""),
    "qbit_senha": os.environ.get("QBIT_SENHA", ""),
    "qbit_categoria": "minerva",
    "qbit_pasta": "",                      # vazio = pasta padrão do qBit (+ categoria)
    "limite_gb": 20,
    "pedidos_por_dia": 5,
    "baixando_max": 1,                    # pedidos baixando ao mesmo tempo; os outros esperam a vez (0 = sem limite)
    "espaco_minimo_gb": 30,               # abaixo disso, pedido novo espera (sem_espaco)
    "aprovacao": False,                   # True: pedidos de usuário comum esperam o admin
    "destino_pc": "Jogos/{plataforma}",   # relativo à pasta de torrents do PC do dono
    "trackers": 10,
}


class ErroPedido(ValueError):
    pass


def preparar(con):
    con.executescript(ESQUEMA)
    if "subpasta" not in {r[1] for r in con.execute("PRAGMA table_info(pedido_arquivos)")}:
        con.execute("ALTER TABLE pedido_arquivos ADD COLUMN subpasta TEXT DEFAULT ''")


def pasta_windows(texto: str) -> str:
    """Nome de pasta seguro no Windows e nos comandos do worker (sem acento, aspas nem caracteres proibidos)."""
    t = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    t = re.sub(r'[<>:"/\\|?*\'`$]', "", t)
    return re.sub(r"\s+", " ", t).strip(" .") or "Jogo"


SUBPASTA = {"dlc": "DLC", "update": "Updates"}


def ajustes(con) -> dict:
    return {k: store.ajuste(con, f"ped_{k}", v) for k, v in PADROES.items()}


def cliente(con) -> Qbit:
    a = ajustes(con)
    return Qbit(a["qbit_url"], a["qbit_usuario"], a["qbit_senha"])


def destino_pc(con, sistema: str) -> str:
    return ajustes(con)["destino_pc"].replace("{plataforma}", sistema)


# ------------------------------------------------------------------------------------------- criar

def criar(con, usuario: dict, jogo_id: int, versao_chave: str, qb: Qbit | None = None,
          extras: list | tuple = ()) -> dict:
    """Pedido de uma versão do jogo e/ou complementos (DLC, title updates) dele. versao_chave vazio = só
    complementos (para quem já tem o jogo). O tamanho dos complementos conta no limite por pedido."""
    a = ajustes(con)
    j = catalogo.jogo(con, jogo_id)
    if not j:
        raise ErroPedido("jogo não existe")
    v = None
    if versao_chave:
        v = next((x for x in j["versoes"] if x["chave"] == versao_chave), None)
        if not v:
            raise ErroPedido("versão não existe")
    comps = catalogo.arquivos_por_id(con, jogo_id, extras)
    if len(comps) != len(set(int(e) for e in extras)):
        raise ErroPedido("DLC/atualização não pertence a este jogo")
    if not v and not comps:
        raise ErroPedido("escolha uma versão ou pelo menos uma DLC/atualização")
    tamanho = (v["tamanho"] if v else 0) + sum(c["tamanho"] for c in comps)
    if tamanho > a["limite_gb"] * GB:
        raise ErroPedido(f"passa do limite por pedido ({tamanho / GB:.1f} GB > {a['limite_gb']} GB) — "
                         "desligue algumas DLCs ou peça em duas vezes")
    if usuario["papel"] != "admin":
        (hoje,) = con.execute("SELECT COUNT(*) FROM pedidos WHERE usuario_id=? AND criado_em>? AND status!='cancelado'",
                              (usuario["id"], time.time() - 86400)).fetchone()
        if hoje >= a["pedidos_por_dia"]:
            raise ErroPedido(f"limite de {a['pedidos_por_dia']} pedidos em 24 h")
    ativos = f"p.status IN ({','.join('?' * len(ATIVOS))})"
    if v:
        dup = con.execute(f"SELECT p.id FROM pedidos p WHERE p.usuario_id=? AND p.jogo_id=? AND p.versao_chave=? AND {ativos}",
                          (usuario["id"], jogo_id, versao_chave, *ATIVOS)).fetchone()
        if dup:
            raise ErroPedido(f"você já pediu essa versão (pedido #{dup[0]})")
    for c in comps:
        dup = con.execute(f"SELECT p.id FROM pedido_arquivos pa JOIN pedidos p ON p.id=pa.pedido_id WHERE p.usuario_id=? "
                          f"AND pa.infohash=? AND pa.so=? AND {ativos}",
                          (usuario["id"], c["infohash"], c["idx"], *ATIVOS)).fetchone()
        if dup:
            raise ErroPedido(f"“{c['rotulo']}” já está no pedido #{dup[0]}")

    arqs = [{**x, "subpasta": ""} for x in (catalogo.arquivos_da_versao(con, jogo_id, versao_chave) if v else [])]
    pasta_jogo = pasta_windows(j["titulo"])
    arqs += [{**c, "subpasta": f"{SUBPASTA[c['tipo']]}/{pasta_jogo}"} for c in comps]
    ndlc = sum(c["tipo"] == "dlc" for c in comps)
    nup = len(comps) - ndlc
    partes = ([f"{ndlc} DLC"] if ndlc else []) + ([f"{nup} atualização" if nup == 1 else f"{nup} atualizações"] if nup else [])
    mais = " + ".join(partes)
    rotulo = (v["rotulo"] + (f" + {mais}" if mais else "")) if v else f"Só {mais}"
    linha = store.arquivo(con, arqs[0]["colecao_id"], arqs[0]["idx"])
    magnet = store.magnet_de(linha, a["trackers"])
    status = "aguardando_aprovacao" if a["aprovacao"] and usuario["papel"] != "admin" else "aguardando_vez"
    agora = time.time()
    cur = con.execute(
        "INSERT INTO pedidos(usuario_id, jogo_id, titulo, plataforma, sistema, versao_chave, versao_rotulo, tamanho, "
        "status, magnet, criado_em, atualizado_em, aprovado_por) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (usuario["id"], jogo_id, j["titulo"], j["plataforma"], j["sistema"], versao_chave or "", rotulo, tamanho,
         status, magnet, agora, agora, usuario["id"] if status == "aguardando_vez" else None))
    pid = cur.lastrowid
    con.executemany("INSERT INTO pedido_arquivos(pedido_id, colecao_id, infohash, so, caminho, tamanho, subpasta) "
                    "VALUES(?,?,?,?,?,?,?)",
                    [(pid, x["colecao_id"], x["infohash"], x["idx"], x["caminho"], x["tamanho"], x["subpasta"])
                     for x in arqs])
    con.commit()
    if status == "aguardando_vez":
        entrar(con, pid, qb or cliente(con))
    return pedido(con, pid)


def aprovar(con, pid: int, admin: dict, qb: Qbit | None = None):
    if con.execute("UPDATE pedidos SET status='aguardando_vez', aprovado_por=?, atualizado_em=? "
                   "WHERE id=? AND status='aguardando_aprovacao'", (admin["id"], time.time(), pid)).rowcount:
        con.commit()
        entrar(con, pid, qb or cliente(con))


def cancelar(con, pid: int, qb: Qbit | None = None):
    p = pedido(con, pid)
    if not p or p["status"] not in ATIVOS or p["status"] == "entregando":
        raise ErroPedido("esse pedido não pode ser cancelado agora")
    _marcar(con, pid, "cancelado")
    if p["status"] in NO_QBIT:
        liberar(con, pid, qb or cliente(con))


# ------------------------------------------------------------------------------------------- qBit

def vagas(con) -> int | None:
    """Quantos pedidos ainda podem ir para o qBit agora (None = sem limite)."""
    maximo = int(ajustes(con)["baixando_max"])
    if maximo <= 0:
        return None
    (n,) = con.execute(f"SELECT COUNT(*) FROM pedidos WHERE status IN ({','.join('?' * len(EM_DOWNLOAD))})",
                       EM_DOWNLOAD).fetchone()
    return max(0, maximo - n)


def entrar(con, pid: int, qb: Qbit) -> bool:
    """Manda o pedido para o qBit se houver vaga; senão ele espera a vez. True se foi enviado (ou tentou)."""
    if vagas(con) == 0:
        _marcar(con, pid, "aguardando_vez", MSG_VEZ)
        return False
    enviar(con, pid, qb)
    return True


def _espaco_ok(con, qb: Qbit, tamanho: int) -> bool:
    livre = qb.espaco_livre()
    if livre is None:
        return True
    (reservado,) = con.execute(
        "SELECT COALESCE(SUM(p.tamanho * (1 - p.progresso)), 0) FROM pedidos p WHERE p.status IN ('na_fila','baixando','pausado') "
    ).fetchone()
    return livre - reservado - tamanho >= ajustes(con)["espaco_minimo_gb"] * GB


def enviar(con, pid: int, qb: Qbit):
    """Põe os arquivos do pedido no qBit: torrent da coleção entra PARADO, todos os arquivos com prioridade 0,
    liga só os pedidos e inicia. Se o torrent já existe (outro pedido da mesma coleção), só liga os arquivos."""
    p = pedido(con, pid)
    a = ajustes(con)
    try:
        if not _espaco_ok(con, qb, p["tamanho"]):
            _marcar(con, pid, "sem_espaco", "pouco espaço no servidor — o pedido começa sozinho quando liberar")
            return
        por_hash: dict = {}
        for f in p["arquivos"]:
            por_hash.setdefault(f["infohash"], []).append(f)
        for infohash, fs in por_hash.items():
            novo = qb.torrent(infohash) is None
            if novo:
                (local, nome) = con.execute("SELECT arquivo_local, nome FROM colecoes WHERE id=?",
                                            (fs[0]["colecao_id"],)).fetchone()
                with open(local, "rb") as fh:
                    conteudo = fh.read()
                qb.criar_categoria(a["qbit_categoria"])
                qb.adicionar_torrent(conteudo, os.path.basename(local), a["qbit_categoria"], "minerva",
                                     a["qbit_pasta"] or None)
                qb.esperar_torrent(infohash)
            lista = qb.arquivos(infohash)
            if novo:
                qb.prioridade(infohash, [x["index"] for x in lista], 0)
            ids = []
            for f in fs:
                i = indice_por_caminho(lista, f["caminho"])
                if i is None:
                    raise ErroPedido(f"arquivo não encontrado no torrent: {f['caminho']}")
                nome_q = next(x["name"] for x in lista if x["index"] == i)
                con.execute("UPDATE pedido_arquivos SET qbit_index=?, qbit_nome=? WHERE pedido_id=? AND infohash=? AND so=?",
                            (i, nome_q, pid, infohash, f["so"]))
                ids.append(i)
            qb.prioridade(infohash, ids, 1)
            qb.iniciar(infohash)
        _marcar(con, pid, "na_fila")
    except (ErroQbit, ErroPedido, OSError) as e:
        log.warning("pedido %s: falha ao enviar: %s", pid, e)
        _marcar(con, pid, "erro", str(e))


def _usados_por_outros(con, pid: int, infohash: str, qbit_index: int | None = None) -> bool:
    sql = (f"SELECT 1 FROM pedido_arquivos pa JOIN pedidos p ON p.id=pa.pedido_id WHERE pa.infohash=? AND p.id!=? "
           f"AND p.status IN ({','.join('?' * len(NO_QBIT))})")
    params = [infohash, pid, *NO_QBIT]
    if qbit_index is not None:
        sql += " AND pa.qbit_index=?"
        params.append(qbit_index)
    return con.execute(sql + " LIMIT 1", params).fetchone() is not None


def liberar(con, pid: int, qb: Qbit) -> dict:
    """Depois de entregue (ou cancelado): desliga os arquivos do pedido no torrent e apaga o torrent se ninguém
    mais usa. Devolve quais arquivos o worker pode apagar do disco (os que nenhum outro pedido usa)."""
    p = pedido(con, pid)
    apagar_arquivos, torrents_apagados = [], []
    for infohash in {f["infohash"] for f in p["arquivos"]}:
        fs = [f for f in p["arquivos"] if f["infohash"] == infohash]
        try:
            if not _usados_por_outros(con, pid, infohash):
                if qb.torrent(infohash):
                    qb.apagar(infohash, com_arquivos=True)
                torrents_apagados.append(infohash)
                continue
            livres = [f for f in fs if f["qbit_index"] is not None
                      and not _usados_por_outros(con, pid, infohash, f["qbit_index"])]
            if livres:
                qb.prioridade(infohash, [f["qbit_index"] for f in livres], 0)
                apagar_arquivos += [f["qbit_nome"] for f in livres if f["qbit_nome"]]
        except ErroQbit as e:
            log.warning("pedido %s: falha ao liberar %s: %s", pid, infohash, e)
    return {"apagar_arquivos": apagar_arquivos, "torrents_apagados": torrents_apagados}


# ------------------------------------------------------------------------------------------- status

def _marcar(con, pid: int, status: str, erro: str | None = None, **extra):
    campos = {"status": status, "erro": erro, "atualizado_em": time.time(), **extra}
    con.execute(f"UPDATE pedidos SET {', '.join(f'{k}=?' for k in campos)} WHERE id=?", (*campos.values(), pid))
    con.commit()


def sincronizar(con, qb: Qbit) -> dict:
    """Atualiza progresso/status dos pedidos no qBit e, se abriu vaga, manda os que esperavam (vez ou espaço)
    por ordem de pedido."""
    resumo = {"atualizados": 0, "reenviados": 0}
    _atualizar(con, qb, resumo)
    enviados = 0
    for (pid,) in con.execute(f"SELECT id FROM pedidos WHERE status IN ({','.join('?' * len(ESPERANDO_VAGA))}) "
                              "ORDER BY id", ESPERANDO_VAGA).fetchall():
        if vagas(con) == 0:
            if pedido(con, pid)["status"] == "sem_espaco":
                _marcar(con, pid, "aguardando_vez", MSG_VEZ)
            continue
        enviar(con, pid, qb)
        enviados += 1
    resumo["reenviados"] = enviados
    if enviados:
        _atualizar(con, qb, resumo)  # já lê o estado no qBit dos que acabaram de entrar
    global _ultima_conferencia
    if time.time() - _ultima_conferencia >= CONFERIR_A_CADA:
        _ultima_conferencia = time.time()
        resumo.update(conferir(con, qb))
    return resumo


def conferir(con, qb: Qbit) -> dict:
    """Arquivos ligados no qBit × pedidos. Nos torrents dos pedidos em andamento:
    - arquivo ligado que nenhum pedido em andamento usa → desliga (prioridade 0) e registra no log. Visto em
      28/11/2026: vizinhos que dividem um pedaço com o arquivo pedido apareciam ligados ("caronas");
    - arquivo de pedido ainda baixando que está desligado e incompleto → religa.
    Não apaga nada do disco (quem apaga é a entrega)."""
    linhas = con.execute(
        f"SELECT pa.infohash, pa.qbit_index, p.status FROM pedido_arquivos pa JOIN pedidos p ON p.id=pa.pedido_id "
        f"WHERE p.status IN ({','.join('?' * len(NO_QBIT))}) AND pa.qbit_index IS NOT NULL", NO_QBIT).fetchall()
    usados: dict = {}
    baixando: dict = {}
    for infohash, idx, status in linhas:
        usados.setdefault(infohash, set()).add(idx)
        if status in EM_DOWNLOAD:
            baixando.setdefault(infohash, set()).add(idx)
    resumo = {"desligados": 0, "religados": 0}
    if not usados:
        return resumo
    existem = {t["hash"] for t in qb.info(list(usados))}
    for infohash in usados.keys() & existem:
        lista = qb.arquivos(infohash)
        sobra = [f for f in lista if f.get("priority", 0) > 0 and f["index"] not in usados[infohash]]
        faltam = [f for f in lista if f["index"] in baixando.get(infohash, set())
                  and f.get("priority", 0) == 0 and f.get("progress", 0) < 1]
        try:
            if sobra:
                qb.prioridade(infohash, [f["index"] for f in sobra], 0)
                log.warning("conferência %s: %d arquivo(s) ligados sem pedido desligados: %s", infohash[:8], len(sobra),
                            "; ".join(f["name"].rsplit("/", 1)[-1][:60] for f in sobra[:10]))
                resumo["desligados"] += len(sobra)
            if faltam:
                qb.prioridade(infohash, [f["index"] for f in faltam], 1)
                log.warning("conferência %s: %d arquivo(s) de pedido estavam desligados, religados: %s", infohash[:8],
                            len(faltam), "; ".join(f["name"].rsplit("/", 1)[-1][:60] for f in faltam[:10]))
                resumo["religados"] += len(faltam)
        except ErroQbit as e:
            log.warning("conferência %s falhou: %s", infohash[:8], e)
    return resumo


def _atualizar(con, qb: Qbit, resumo: dict):
    ativos = con.execute("SELECT id FROM pedidos WHERE status IN ('na_fila','baixando','pausado')").fetchall()
    if not ativos:
        return
    ped = {pid: pedido(con, pid) for (pid,) in ativos}
    hashes = {f["infohash"] for p in ped.values() for f in p["arquivos"]}
    torrents = {t["hash"]: t for t in qb.info(list(hashes))}
    arquivos_cache: dict = {}
    for pid, p in ped.items():
        total, feito, vel, estados, sumiu, hs = 0, 0.0, 0, set(), False, set()
        for f in p["arquivos"]:
            t = torrents.get(f["infohash"])
            if not t:
                sumiu = True
                break
            if f["infohash"] not in arquivos_cache:
                arquivos_cache[f["infohash"]] = {x["index"]: x for x in qb.arquivos(f["infohash"])}
            qf = arquivos_cache[f["infohash"]].get(f["qbit_index"])
            prog = qf["progress"] if qf else 0.0
            con.execute("UPDATE pedido_arquivos SET progresso=? WHERE pedido_id=? AND infohash=? AND so=?",
                        (prog, pid, f["infohash"], f["so"]))
            total += f["tamanho"]
            feito += f["tamanho"] * prog
            if f["infohash"] not in hs:
                vel += t.get("dlspeed", 0)
                hs.add(f["infohash"])
            estados.add(t.get("state", ""))
        if sumiu:
            _marcar(con, pid, "erro", "o torrent foi removido do qBittorrent")
            resumo["atualizados"] += 1
            continue
        prog = feito / total if total else 0
        if prog >= 0.9999:
            novo = "baixado"
        elif estados & {"error", "missingFiles"}:
            novo = "erro"
        elif estados <= {"stoppedDL", "pausedDL"}:
            novo = "pausado"
        elif prog > 0 or estados & {"downloading", "forcedDL"}:
            novo = "baixando"
        else:
            novo = "na_fila"
        extra = {"progresso": round(prog, 4), "velocidade": vel}
        if novo == "baixado":
            extra["baixado_em"] = time.time()
        _marcar(con, pid, novo, "erro no qBittorrent (arquivos sumiram?)" if novo == "erro" else None, **extra)
        resumo["atualizados"] += 1


# ------------------------------------------------------------------------------------------- leitura

def pedido(con, pid: int) -> dict | None:
    r = con.execute("SELECT p.*, u.nome AS usuario, u.pc FROM pedidos p JOIN usuarios u ON u.id=p.usuario_id "
                    "WHERE p.id=?", (pid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["arquivos"] = [dict(x) for x in con.execute(
        "SELECT * FROM pedido_arquivos WHERE pedido_id=? ORDER BY caminho", (pid,))]
    d["so"] = [f["so"] for f in d["arquivos"]]
    return d


def listar(con, usuario_id: int | None = None, limite: int = 100):
    sql = ("SELECT p.id, p.jogo_id, p.titulo, p.plataforma, p.versao_rotulo, p.tamanho, p.status, p.progresso, "
           "p.velocidade, p.erro, p.destino, p.criado_em, p.entregue_em, u.nome AS usuario "
           "FROM pedidos p JOIN usuarios u ON u.id=p.usuario_id ")
    params: list = []
    if usuario_id:
        sql += "WHERE p.usuario_id=? "
        params.append(usuario_id)
    sql += "ORDER BY p.id DESC LIMIT ?"
    return [dict(r) for r in con.execute(sql, [*params, limite])]


def do_jogo(con, jogo_id: int, usuario_id: int):
    return [dict(r) for r in con.execute(
        "SELECT id, versao_chave, versao_rotulo, status, progresso, erro, magnet, destino FROM pedidos "
        "WHERE jogo_id=? AND usuario_id=? ORDER BY id DESC", (jogo_id, usuario_id))]


# ------------------------------------------------------------------------------------------- worker

def entregas_pendentes(con) -> list:
    """O que o worker do host deve copiar: pedidos baixados (e 'entregando' parados há mais de 2 h)."""
    ids = con.execute("SELECT id FROM pedidos WHERE status='baixado' OR (status='entregando' AND atualizado_em<?) "
                      "ORDER BY id", (time.time() - 7200,)).fetchall()
    saida = []
    for (pid,) in ids:
        p = pedido(con, pid)
        saida.append({"id": pid, "usuario": p["usuario"], "pc": p["pc"], "titulo": p["titulo"],
                      "plataforma": p["plataforma"], "versao": p["versao_rotulo"], "tamanho": p["tamanho"],
                      "destino_relativo": destino_pc(con, p["sistema"]),
                      "arquivos": [{"infohash": f["infohash"], "qbit_nome": f["qbit_nome"], "tamanho": f["tamanho"],
                                    "nome": f["caminho"].rsplit("/", 1)[-1], "subpasta": f.get("subpasta") or ""}
                                   for f in p["arquivos"]]})
    return saida


def relatar_entrega(con, pid: int, status: str, mensagem: str | None, destino: str | None, qb: Qbit) -> dict:
    if status not in ("entregando", "entregue", "erro", "baixado"):
        raise ErroPedido("status inválido")
    p = pedido(con, pid)
    if not p:
        raise ErroPedido("pedido não existe")
    extra = {"destino": destino} if destino else {}
    if status == "entregue":
        extra["entregue_em"] = time.time()
        extra["progresso"] = 1
    _marcar(con, pid, status, mensagem if status in ("erro", "baixado") else None, **extra)  # baixado+msg = aguardando
    return liberar(con, pid, qb) if status == "entregue" else {}
