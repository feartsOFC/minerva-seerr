"""Banco SQLite do índice: coleções, arquivos e busca FTS5 (trigram)."""
import json
import re
import sqlite3
import time

from .magnet import limitar_trackers, montar_magnet
from .torrent import Torrent, acervo_de

ESQUEMA = """
CREATE TABLE IF NOT EXISTS colecoes (
    id            INTEGER PRIMARY KEY,
    url           TEXT UNIQUE NOT NULL,
    nome          TEXT NOT NULL,          -- "Redump - Microsoft - Xbox 360"
    acervo        TEXT,                   -- "Redump"
    arquivo_local TEXT,
    infohash      TEXT,
    nome_interno  TEXT,                   -- info.name ("Minerva_Myrient")
    trackers      TEXT,                   -- JSON
    qtd_arquivos  INTEGER DEFAULT 0,
    total_entradas INTEGER DEFAULT 0,     -- info.files com pads
    tamanho_total INTEGER DEFAULT 0,
    indexado_em   REAL
);
CREATE TABLE IF NOT EXISTS arquivos (
    id         INTEGER PRIMARY KEY,
    colecao_id INTEGER NOT NULL REFERENCES colecoes(id) ON DELETE CASCADE,
    idx        INTEGER NOT NULL,          -- valor do so=
    caminho    TEXT NOT NULL,
    nome       TEXT NOT NULL,
    tamanho    INTEGER NOT NULL,
    UNIQUE (colecao_id, idx)
);
CREATE INDEX IF NOT EXISTS ix_arq_colecao ON arquivos(colecao_id);
CREATE TABLE IF NOT EXISTS ajustes (chave TEXT PRIMARY KEY, valor TEXT);
"""

FTS = ("CREATE VIRTUAL TABLE IF NOT EXISTS busca USING fts5("
       "nome, content='arquivos', content_rowid='id', tokenize='trigram')")


def conectar(caminho: str) -> sqlite3.Connection:
    con = sqlite3.connect(caminho, timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(ESQUEMA)
    con.execute(FTS)
    con.create_function("palavra", 2, _palavra, deterministic=True)
    from ..catalog import catalogo  # import tardio: catalogo depende só de sqlite
    from .. import pedidos, usuarios
    catalogo.preparar(con)
    usuarios.preparar(con)
    pedidos.preparar(con)
    return con


def _palavra(termo, texto) -> int:
    """Termo curto ("3", "ii") casa só como palavra inteira."""
    if termo is None or texto is None:
        return 0
    return 1 if re.search(r"(?<![0-9a-z])" + re.escape(termo) + r"(?![0-9a-z])", texto, re.IGNORECASE) else 0


def gravar_colecao(con, url: str, nome: str, arquivo_local: str, t: Torrent) -> int:
    """Insere/atualiza a coleção e troca todos os arquivos dela. Mantém o mesmo id da coleção."""
    tamanho = sum(a.tamanho for a in t.arquivos)
    con.execute(
        "INSERT INTO colecoes(url, nome, acervo, arquivo_local, infohash, nome_interno, trackers,"
        " qtd_arquivos, total_entradas, tamanho_total, indexado_em) VALUES(?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(url) DO UPDATE SET nome=excluded.nome, acervo=excluded.acervo,"
        " arquivo_local=excluded.arquivo_local, infohash=excluded.infohash, nome_interno=excluded.nome_interno,"
        " trackers=excluded.trackers, qtd_arquivos=excluded.qtd_arquivos, total_entradas=excluded.total_entradas,"
        " tamanho_total=excluded.tamanho_total, indexado_em=excluded.indexado_em",
        (url, nome, acervo_de(nome), arquivo_local, t.infohash, t.nome, json.dumps(t.trackers),
         len(t.arquivos), t.total_entradas, tamanho, time.time()),
    )
    cid = con.execute("SELECT id FROM colecoes WHERE url=?", (url,)).fetchone()[0]
    _apagar_arquivos(con, cid)
    con.executemany(
        "INSERT INTO arquivos(colecao_id, idx, caminho, nome, tamanho) VALUES(?,?,?,?,?)",
        [(cid, a.idx, a.caminho, a.nome, a.tamanho) for a in t.arquivos],
    )
    con.execute(
        "INSERT INTO busca(rowid, nome) SELECT id, nome FROM arquivos WHERE colecao_id=?", (cid,))
    from ..catalog import catalogo
    catalogo.reconstruir(con, cid)
    return cid


def _apagar_arquivos(con, cid: int):
    # FTS com content externo: remover do índice antes de apagar as linhas
    con.execute(
        "INSERT INTO busca(busca, rowid, nome) SELECT 'delete', id, nome FROM arquivos WHERE colecao_id=?", (cid,))
    con.execute("DELETE FROM arquivos WHERE colecao_id=?", (cid,))


def remover_colecao(con, cid: int):
    _apagar_arquivos(con, cid)
    con.execute("DELETE FROM colecoes WHERE id=?", (cid,))


def buscar(con, texto: str, colecao: str | None = None, colecao_ids=None, limite: int = 100, offset: int = 0):
    """Todos os termos precisam aparecer no nome do arquivo. Termos com 3+ letras usam o FTS
    trigram (casam em qualquer parte); termos curtos casam como palavra inteira."""
    termos = [t for t in re.split(r"\s+", texto.strip()) if t]
    longos = [t for t in termos if len(t) >= 3]
    curtos = [t for t in termos if len(t) < 3]
    sel = ("SELECT a.id, a.colecao_id, a.idx, a.caminho, a.nome, a.tamanho, c.nome AS colecao, c.acervo "
           "FROM arquivos a JOIN colecoes c ON c.id=a.colecao_id ")
    params: list = []
    if longos:
        sel += "WHERE a.id IN (SELECT rowid FROM busca WHERE busca MATCH ?) "
        params.append(" AND ".join('"' + t.replace('"', '""') + '"' for t in longos))
    else:
        sel += "WHERE 1=1 "
    for t in curtos:
        sel += "AND palavra(?, a.nome) "
        params.append(t)
    if colecao:
        sel += "AND c.nome LIKE ? "
        params.append(f"%{colecao}%")
    if colecao_ids:
        sel += f"AND a.colecao_id IN ({','.join('?' * len(colecao_ids))}) "
        params += list(colecao_ids)
    sel += "ORDER BY c.nome, a.nome LIMIT ? OFFSET ?"
    params += [limite, offset]
    return [dict(r) for r in con.execute(sel, params)]


def arquivo(con, colecao_id: int, idx: int):
    r = con.execute(
        "SELECT a.id, a.colecao_id, a.idx, a.caminho, a.nome, a.tamanho, c.nome AS colecao, c.infohash,"
        " c.trackers, c.nome_interno, c.arquivo_local FROM arquivos a JOIN colecoes c ON c.id=a.colecao_id"
        " WHERE a.colecao_id=? AND a.idx=?", (colecao_id, idx)).fetchone()
    return dict(r) if r else None


def magnet_de(linha: dict, trackers: int | None = 10) -> str:
    trs = limitar_trackers(json.loads(linha.get("trackers") or "[]"), trackers)
    return montar_magnet(linha["infohash"], linha["idx"], trs, linha.get("nome_interno") or "Minerva_Myrient")


def colecoes(con):
    return [dict(r) for r in con.execute(
        "SELECT id, nome, acervo, infohash, qtd_arquivos, total_entradas, tamanho_total, indexado_em, url"
        " FROM colecoes ORDER BY nome")]


def stats(con) -> dict:
    nc, = con.execute("SELECT COUNT(*) FROM colecoes").fetchone()
    na, tam = con.execute("SELECT COUNT(*), COALESCE(SUM(tamanho),0) FROM arquivos").fetchone()
    ult, = con.execute("SELECT MAX(indexado_em) FROM colecoes").fetchone()
    return {"colecoes": nc, "arquivos": na, "tamanho_total": tam, "ultima_atualizacao": ult}


def ajuste(con, chave: str, padrao=None):
    r = con.execute("SELECT valor FROM ajustes WHERE chave=?", (chave,)).fetchone()
    return json.loads(r[0]) if r else padrao


def definir_ajuste(con, chave: str, valor):
    con.execute("INSERT INTO ajustes(chave, valor) VALUES(?,?) ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor",
                (chave, json.dumps(valor)))
    con.commit()
