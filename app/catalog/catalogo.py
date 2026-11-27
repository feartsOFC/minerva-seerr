"""Catálogo: plataformas, jogos (agrupados) e versões, em cima das tabelas do indexador."""
import re
import sqlite3

from .titles import analisar, chave_titulo, peso_versao

ESQUEMA = """
CREATE TABLE IF NOT EXISTS plataformas (
    id            INTEGER PRIMARY KEY,
    sistema       TEXT UNIQUE NOT NULL,     -- "Microsoft - Xbox 360" (nome de pasta e do libretro)
    nome          TEXT NOT NULL,            -- "Xbox 360"
    curto         TEXT NOT NULL,            -- selo no card: "X360"
    libretro_repo TEXT,                     -- "Microsoft_-_Xbox_360"
    igdb_id       INTEGER
);
CREATE TABLE IF NOT EXISTS jogos (
    id             INTEGER PRIMARY KEY,
    plataforma_id  INTEGER NOT NULL REFERENCES plataformas(id),
    chave          TEXT NOT NULL,
    titulo         TEXT NOT NULL,
    so_extras      INTEGER DEFAULT 0,       -- 1 = só tem demo/beta/bonus
    qtd_versoes    INTEGER DEFAULT 0,
    tamanho_princ  INTEGER DEFAULT 0,       -- tamanho da versão principal (todos os discos)
    capa_fonte     TEXT,                    -- libretro | igdb | screenscraper
    capa_ref       TEXT,                    -- nome no libretro ou image_id do IGDB
    capa_incerta   INTEGER DEFAULT 0,
    capa_buscada_em REAL,
    UNIQUE (plataforma_id, chave)
);
CREATE TABLE IF NOT EXISTS metadados (
    jogo_id     INTEGER PRIMARY KEY REFERENCES jogos(id) ON DELETE CASCADE,
    fonte       TEXT,                       -- igdb | screenscraper
    externo_id  TEXT,
    nome_fonte  TEXT,                       -- nome como a fonte chama o jogo
    sinopse     TEXT,
    generos     TEXT,
    ano         INTEGER,
    nota        REAL,
    incerto     INTEGER DEFAULT 0,
    buscado_em  REAL
);
"""
COLUNAS_ARQUIVO = {"jogo_id": "INTEGER", "versao_chave": "TEXT", "versao_rotulo": "TEXT", "regiao": "TEXT",
                   "revisao": "TEXT", "disco": "TEXT", "tipo": "TEXT"}

# Nome amigável, selo e id de plataforma no IGDB. Sistemas fora da lista usam o nome derivado.
CONHECIDAS = {
    "Microsoft - Xbox 360": ("Xbox 360", "X360", 12),
    "Microsoft - Xbox": ("Xbox", "XBOX", 11),
    "Sony - PlayStation 2": ("PlayStation 2", "PS2", 8),
    "Sony - PlayStation": ("PlayStation", "PS1", 7),
    "Sony - PlayStation 3": ("PlayStation 3", "PS3", 9),
    "Sony - PlayStation Portable": ("PSP", "PSP", 38),
    "Nintendo - Super Nintendo Entertainment System": ("Super Nintendo", "SNES", 19),
    "Nintendo - Nintendo 64": ("Nintendo 64", "N64", 4),
    "Nintendo - Nintendo Entertainment System": ("NES", "NES", 18),
    "Nintendo - Game Boy": ("Game Boy", "GB", 33),
    "Nintendo - Game Boy Color": ("Game Boy Color", "GBC", 22),
    "Nintendo - Game Boy Advance": ("Game Boy Advance", "GBA", 24),
    "Nintendo - GameCube": ("GameCube", "GC", 21),
    "Nintendo - Wii": ("Wii", "WII", 5),
    "Nintendo - Nintendo DS": ("Nintendo DS", "NDS", 20),
    "Sega - Mega Drive - Genesis": ("Mega Drive", "MD", 29),
    "Sega - Dreamcast": ("Dreamcast", "DC", 23),
}
# Coleções de complementos: os arquivos NÃO viram jogos, ficam presos ao jogo-base (DLC e title updates).
COMPLEMENTOS = re.compile(r"\((Digital|Title Updates)\)$")
TIPOS_COMPLEMENTO = ("dlc", "update")
NAO_VERSAO = "COALESCE(a.tipo,'') NOT IN ('dlc','update','avulso')"  # filtro SQL: só arquivos que são versão do jogo
QUALIFICADORES = re.compile(r"^(Non-Redump|Unofficial|Source Code|RA|Private)\s*-\s*", re.I)


def preparar(con: sqlite3.Connection):
    con.executescript(ESQUEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(arquivos)")}
    for c, t in COLUNAS_ARQUIVO.items():
        if c not in cols:
            con.execute(f"ALTER TABLE arquivos ADD COLUMN {c} {t}")
    if "plataforma_id" not in {r[1] for r in con.execute("PRAGMA table_info(colecoes)")}:
        con.execute("ALTER TABLE colecoes ADD COLUMN plataforma_id INTEGER")
    con.execute("CREATE INDEX IF NOT EXISTS ix_arq_jogo ON arquivos(jogo_id)")


def sistema_de(colecao: str) -> str:
    """'No-Intro - Nintendo - Nintendo 64 (BigEndian)' → 'Nintendo - Nintendo 64'."""
    partes = colecao.split(" - ", 1)
    resto = partes[1] if len(partes) > 1 else partes[0]
    while QUALIFICADORES.match(resto):
        resto = QUALIFICADORES.sub("", resto, count=1)
    return re.sub(r"(\s*\([^)]*\))+$", "", resto).strip()


def garantir_plataforma(con, sistema: str) -> int:
    r = con.execute("SELECT id FROM plataformas WHERE sistema=?", (sistema,)).fetchone()
    if r:
        return r[0]
    nome, curto, igdb = CONHECIDAS.get(sistema, (sistema.split(" - ")[-1], sistema.split(" - ")[-1][:5].upper(), None))
    cur = con.execute("INSERT INTO plataformas(sistema, nome, curto, libretro_repo, igdb_id) VALUES(?,?,?,?,?)",
                      (sistema, nome, curto, sistema.replace(" ", "_"), igdb))
    return cur.lastrowid


def reconstruir(con, colecao_id: int):
    """Analisa todos os arquivos da coleção, cria/atualiza os jogos e grava a versão em cada arquivo."""
    preparar(con)
    (nome,) = con.execute("SELECT nome FROM colecoes WHERE id=?", (colecao_id,)).fetchone()
    pid = garantir_plataforma(con, sistema_de(nome))
    con.execute("UPDATE colecoes SET plataforma_id=? WHERE id=?", (pid, colecao_id))
    if not COMPLEMENTOS.search(nome):
        _reconstruir_jogos(con, colecao_id, pid)
    ligar_complementos(con, pid)  # jogo novo pode ganhar DLC; coleção de DLC nova liga aos jogos existentes


ROMANOS = {"ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7", "viii": "8", "ix": "9", "x": "10"}


def _norm(chave: str) -> str:
    """'assassins creed ii' → 'assassins creed 2' (DLC e title updates às vezes usam o número arábico)."""
    return " ".join(ROMANOS.get(t, t) for t in chave.split())


class _Jogos:
    """Procura o jogo-base: igual (com romanos normalizados) ou, se não houver, o ÚNICO jogo cujo título começa
    com o nome procurado ('Minecraft' → 'Minecraft - Xbox 360 Edition'; dois candidatos = não liga)."""

    def __init__(self, pares):
        self.exato = {}
        for jid, chave in pares:
            self.exato.setdefault(_norm(chave), jid)
        self.chaves = sorted(self.exato)

    def achar(self, chave: str):
        n = _norm(chave)
        if n in self.exato:
            return self.exato[n]
        import bisect
        i = bisect.bisect_left(self.chaves, n + " ")
        achados = []
        while i < len(self.chaves) and self.chaves[i].startswith(n + " "):
            achados.append(self.chaves[i])
            i += 1
        return self.exato[achados[0]] if len(achados) == 1 else None


def _base_do_complemento(titulo: str, jogos: "_Jogos"):
    """'Guitar Hero III - Legends of Rock - Muse Track Pack' → (id do Guitar Hero III, 'Muse Track Pack').
    Testa do prefixo mais longo ao mais curto, cortando em ' - '; primeiro igual, depois por começo único."""
    partes = titulo.split(" - ")
    for i in range(len(partes), 0, -1):
        jid = jogos.exato.get(_norm(chave_titulo(" - ".join(partes[:i]))))
        if jid:
            return jid, " - ".join(partes[i:])
    for i in range(len(partes), 0, -1):
        jid = jogos.achar(chave_titulo(" - ".join(partes[:i])))
        if jid:
            return jid, " - ".join(partes[i:])
    return None, None


def ligar_complementos(con, plataforma_id: int):
    """Prende DLC/Addon (coleção Digital) e title updates ao jogo-base da mesma plataforma.
    Arquivos sem jogo-base (ex.: jogos XBLA) ficam fora do catálogo (tipo 'avulso')."""
    cols = con.execute("SELECT id, nome FROM colecoes WHERE plataforma_id=?", (plataforma_id,)).fetchall()
    comp = [(cid, n) for cid, n in cols if COMPLEMENTOS.search(n)]
    if not comp:
        return
    jogos = _Jogos(con.execute("SELECT id, chave FROM jogos WHERE plataforma_id=? AND qtd_versoes>0 AND so_extras=0",
                               (plataforma_id,)).fetchall())
    for cid, nome_col in comp:
        updates = "Title Updates" in nome_col
        linhas = []
        for aid, nome_arq in con.execute("SELECT id, nome FROM arquivos WHERE colecao_id=?", (cid,)).fetchall():
            a = analisar(nome_arq)
            extras = {e.lower() for e in a.extras}
            jid, resto = None, None
            if updates and a.tipo == "jogo" and a.revisao:
                jid, tipo = jogos.achar(a.chave), "update"
                rotulo = " · ".join(x for x in (a.revisao, a.regiao, a.idiomas) if x)
            elif not updates and extras & {"dlc", "addon"}:
                jid, resto = _base_do_complemento(a.titulo, jogos)
                tipo = "dlc"
                ids = [e for e in a.extras if re.fullmatch(r"[0-9A-F]{8}", e)]
                rotulo = resto or ("Conteúdo extra" + (f" ({ids[0]})" if ids else ""))
            if jid:
                linhas.append((jid, f"{tipo}:{aid}", rotulo, a.regiao, a.revisao, None, tipo, aid))
            else:
                linhas.append((None, None, None, a.regiao, a.revisao, None, "avulso", aid))
        con.executemany("UPDATE arquivos SET jogo_id=?, versao_chave=?, versao_rotulo=?, regiao=?, revisao=?, disco=?, "
                        "tipo=? WHERE id=?", linhas)


def _reconstruir_jogos(con, colecao_id: int, pid: int):

    arquivos = con.execute("SELECT id, nome FROM arquivos WHERE colecao_id=?", (colecao_id,)).fetchall()
    por_jogo: dict = {}
    for aid, nome_arq in arquivos:
        a = analisar(nome_arq)
        por_jogo.setdefault(a.chave, []).append((aid, a))

    for chave, itens in por_jogo.items():
        titulo = min(itens, key=lambda x: peso_versao(x[1]))[1].titulo
        con.execute("INSERT INTO jogos(plataforma_id, chave, titulo) VALUES(?,?,?) "
                    "ON CONFLICT(plataforma_id, chave) DO UPDATE SET titulo=excluded.titulo", (pid, chave, titulo))
        (jid,) = con.execute("SELECT id FROM jogos WHERE plataforma_id=? AND chave=?", (pid, chave)).fetchone()
        con.executemany(
            "UPDATE arquivos SET jogo_id=?, versao_chave=?, versao_rotulo=?, regiao=?, revisao=?, disco=?, tipo=? "
            "WHERE id=?",
            [(jid, a.versao_chave, a.versao_rotulo, a.regiao, a.revisao, a.disco, a.tipo, aid) for aid, a in itens])
    atualizar_resumos(con, pid)


def atualizar_resumos(con, plataforma_id: int):
    """Recalcula qtd de versões, tamanho da principal e se o jogo só tem extras (demo/beta/bonus)."""
    linhas = con.execute(
        "SELECT a.jogo_id, a.versao_chave, a.nome, a.tamanho, a.tipo FROM arquivos a JOIN jogos j ON j.id=a.jogo_id "
        f"WHERE j.plataforma_id=? AND {NAO_VERSAO}", (plataforma_id,)).fetchall()
    jogos: dict = {}
    for jid, vchave, nome, tam, tipo in linhas:
        v = jogos.setdefault(jid, {}).setdefault(vchave, {"tam": 0, "nome": nome})
        v["tam"] += tam
    for jid, versoes in jogos.items():
        principal = min(versoes.values(), key=lambda v: peso_versao(analisar(v["nome"])))
        so_extras = all(analisar(v["nome"]).tipo != "jogo" for v in versoes.values())
        con.execute("UPDATE jogos SET qtd_versoes=?, tamanho_princ=?, so_extras=? WHERE id=?",
                    (len(versoes), principal["tam"], int(so_extras), jid))


def plataformas(con):
    return [dict(r) for r in con.execute(
        "SELECT p.id, p.sistema, p.nome, p.curto, COUNT(DISTINCT j.id) AS jogos FROM plataformas p "
        "JOIN jogos j ON j.plataforma_id=p.id JOIN arquivos a ON a.jogo_id=j.id GROUP BY p.id ORDER BY p.nome")]


def listar_jogos(con, q: str = "", plataforma_id: int | None = None, incluir_extras: bool = False,
                 limite: int = 60, offset: int = 0, ordem: str = "titulo"):
    sql = ("SELECT j.id, j.titulo, j.qtd_versoes, j.tamanho_princ, j.so_extras, j.capa_fonte, j.capa_incerta, "
           "p.id AS plataforma_id, p.nome AS plataforma, p.curto, m.ano, m.nota, m.generos "
           "FROM jogos j JOIN plataformas p ON p.id=j.plataforma_id LEFT JOIN metadados m ON m.jogo_id=j.id "
           "WHERE j.qtd_versoes > 0 ")
    params: list = []
    termos = [t for t in re.split(r"\s+", q.strip()) if t]
    longos = [t for t in termos if len(t) >= 3]
    curtos = [t for t in termos if len(t) < 3]
    if longos:
        sql += ("AND j.id IN (SELECT a.jogo_id FROM arquivos a WHERE a.id IN "
                "(SELECT rowid FROM busca WHERE busca MATCH ?)) ")
        params.append(" AND ".join('"' + t.replace('"', '""') + '"' for t in longos))
    for t in curtos:
        sql += "AND palavra(?, j.titulo) "
        params.append(t)
    if plataforma_id:
        sql += "AND j.plataforma_id=? "
        params.append(plataforma_id)
    if not incluir_extras:
        sql += "AND j.so_extras=0 "
    sql += {"titulo": "ORDER BY j.titulo COLLATE NOCASE ",
            "nota": "ORDER BY m.nota IS NULL, m.nota DESC, j.titulo ",
            "ano": "ORDER BY m.ano IS NULL, m.ano DESC, j.titulo "}.get(ordem, "ORDER BY j.titulo ")
    sql += "LIMIT ? OFFSET ?"
    params += [limite, offset]
    return [dict(r) for r in con.execute(sql, params)]


def jogo(con, jogo_id: int):
    r = con.execute(
        "SELECT j.*, p.nome AS plataforma, p.curto, p.sistema, p.igdb_id, p.libretro_repo, "
        "m.fonte AS meta_fonte, m.sinopse, m.generos, m.ano, m.nota, m.incerto AS meta_incerta, m.nome_fonte "
        "FROM jogos j JOIN plataformas p ON p.id=j.plataforma_id LEFT JOIN metadados m ON m.jogo_id=j.id "
        "WHERE j.id=?", (jogo_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    arqs = con.execute(
        "SELECT a.colecao_id, a.idx, a.nome, a.tamanho, a.versao_chave, a.versao_rotulo, a.tipo, a.disco, "
        "c.nome AS colecao FROM arquivos a JOIN colecoes c ON c.id=a.colecao_id WHERE a.jogo_id=? "
        f"AND {NAO_VERSAO} ORDER BY a.nome", (jogo_id,)).fetchall()
    versoes: dict = {}
    for a in arqs:
        v = versoes.setdefault(a["versao_chave"], {"chave": a["versao_chave"], "rotulo": a["versao_rotulo"],
                                                   "tipo": a["tipo"], "tamanho": 0, "arquivos": [],
                                                   "colecao": a["colecao"]})
        v["tamanho"] += a["tamanho"]
        v["arquivos"].append({"colecao_id": a["colecao_id"], "idx": a["idx"], "nome": a["nome"],
                              "tamanho": a["tamanho"], "disco": a["disco"]})
    lista = sorted(versoes.values(), key=lambda v: peso_versao(analisar(v["arquivos"][0]["nome"])))
    d["versoes"] = lista
    d["principal"] = lista[0]["chave"] if lista else None
    d["dlcs"], d["atualizacoes"] = complementos(con, jogo_id)
    return d


def _num_rev(rev: str | None) -> tuple:
    return tuple(int(n) for n in re.findall(r"\d+", rev or "")) or (0,)


def complementos(con, jogo_id: int):
    """(dlcs, atualizacoes) do jogo. Atualizações: a mais nova de cada região/idioma vem marcada como 'recente'."""
    linhas = [dict(r) for r in con.execute(
        "SELECT a.id, a.nome, a.tamanho, a.versao_rotulo AS rotulo, a.tipo, a.regiao, a.revisao, c.nome AS colecao "
        "FROM arquivos a JOIN colecoes c ON c.id=a.colecao_id WHERE a.jogo_id=? AND a.tipo IN ('dlc','update')",
        (jogo_id,))]
    dlcs = sorted((x for x in linhas if x["tipo"] == "dlc"), key=lambda x: x["rotulo"].lower())
    ups = sorted((x for x in linhas if x["tipo"] == "update"),
                 key=lambda x: (x["regiao"] or "", x["rotulo"].split(" · ", 1)[-1], tuple(-n for n in _num_rev(x["revisao"]))))
    vistos = set()
    for u in ups:
        grupo = u["rotulo"].split(" · ", 1)[-1]
        u["recente"] = grupo not in vistos and "(Alt)" not in u["nome"]
        vistos.add(grupo)
    return dlcs, ups


def arquivos_por_id(con, jogo_id: int, ids):
    """Arquivos de complemento (DLC/update) do jogo, pelo id na tabela arquivos; ignora ids de outro jogo."""
    ids = [int(i) for i in ids]
    if not ids:
        return []
    return [dict(r) for r in con.execute(
        "SELECT a.id, a.colecao_id, a.idx, a.nome, a.caminho, a.tamanho, a.disco, a.tipo, a.versao_rotulo AS rotulo, "
        "c.infohash, c.nome AS colecao FROM arquivos a JOIN colecoes c ON c.id=a.colecao_id "
        f"WHERE a.jogo_id=? AND a.tipo IN ('dlc','update') AND a.id IN ({','.join('?' * len(ids))}) ORDER BY a.nome",
        (jogo_id, *ids))]


def arquivos_da_versao(con, jogo_id: int, versao_chave: str):
    return [dict(r) for r in con.execute(
        "SELECT a.colecao_id, a.idx, a.nome, a.caminho, a.tamanho, a.disco, c.infohash, c.nome AS colecao "
        f"FROM arquivos a JOIN colecoes c ON c.id=a.colecao_id WHERE a.jogo_id=? AND a.versao_chave=? AND {NAO_VERSAO} "
        "ORDER BY a.nome", (jogo_id, versao_chave))]
