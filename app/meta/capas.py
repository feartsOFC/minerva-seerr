"""Capas e metadados: decide a fonte, guarda em cache e nunca repete chamada já feita.

Fontes: 1) libretro (nome exato, sem chave)  2) IGDB (sinopse/gênero/ano/nota e capa dos modernos)
        3) ScreenScraper (opcional, desligado — ainda não implementado).
Falhas também ficam registradas (capa_buscada_em) e só são tentadas de novo depois de 7 dias.
"""
import html
import logging
import os
import threading
import time
import urllib.request

from .. import config
from ..catalog import catalogo
from ..indexer import store
from . import igdb as igdb_mod
from . import libretro

log = logging.getLogger("minerva.capas")
REPETIR_FALHA = 7 * 86400
REPETIR_META = 30 * 86400
_travas: dict = {}
_travas_lock = threading.Lock()
_cliente_igdb = {"chave": None, "cli": None}


def _trava(jid: int) -> threading.Lock:
    with _travas_lock:
        return _travas.setdefault(jid, threading.Lock())


def ajustes(con) -> dict:
    """Ajustes de capas: tela de configurações (banco) com o .env como padrão."""
    return {
        "libretro": store.ajuste(con, "capas_libretro", True),
        "igdb": store.ajuste(con, "capas_igdb", bool(os.environ.get("IGDB_CLIENT_ID"))),
        "igdb_client_id": store.ajuste(con, "igdb_client_id", None) or os.environ.get("IGDB_CLIENT_ID", ""),
        "igdb_client_secret": store.ajuste(con, "igdb_client_secret", None) or os.environ.get("IGDB_CLIENT_SECRET", ""),
        "screenscraper": store.ajuste(con, "capas_screenscraper", False),
    }


def cliente_igdb(con):
    a = ajustes(con)
    if not (a["igdb"] and a["igdb_client_id"] and a["igdb_client_secret"]):
        return None
    chave = (a["igdb_client_id"], a["igdb_client_secret"])
    if _cliente_igdb["chave"] != chave:  # reaproveita o token entre requisições
        _cliente_igdb.update(chave=chave, cli=igdb_mod.IGDB(*chave))
    return _cliente_igdb["cli"]


# ---------------------------------------------------------------------------------------------- libretro

def casar_libretro(con, plataforma_id: int | None = None, baixar_lista=None) -> dict:
    """Casa em lote (local) os jogos sem capa definida com a lista do libretro. Não baixa imagem."""
    if not ajustes(con)["libretro"]:
        return {"casados": 0, "incertos": 0}
    pasta = os.path.join(config.PASTA_CAPAS, "_listas")
    total = {"casados": 0, "incertos": 0, "sem": 0}
    plats = con.execute("SELECT id, libretro_repo FROM plataformas" + (" WHERE id=?" if plataforma_id else ""),
                        (plataforma_id,) if plataforma_id else ()).fetchall()
    for pid, repo in plats:
        nomes = libretro.lista(repo, pasta, **({"baixar": baixar_lista} if baixar_lista else {}))
        if not nomes:
            continue
        ind = libretro.Indice(nomes)
        jogos = con.execute("SELECT j.id, group_concat(a.nome, char(10)) FROM jogos j JOIN arquivos a ON a.jogo_id=j.id "
                            "WHERE j.plataforma_id=? AND (j.capa_fonte IS NULL OR j.capa_fonte='libretro') "
                            "GROUP BY j.id", (pid,)).fetchall()
        for jid, nomes_arq in jogos:
            r = ind.casar(nomes_arq.split("\n"))
            if r:
                con.execute("UPDATE jogos SET capa_fonte='libretro', capa_ref=?, capa_incerta=? WHERE id=?",
                            (r[0], int(r[1]), jid))
                total["casados"] += 1
                total["incertos"] += r[1]
            else:
                total["sem"] += 1
        con.commit()
    log.info("libretro: %s", total)
    return total


# ---------------------------------------------------------------------------------------------- IGDB

def enriquecer(con, jogo_id: int, forcar: bool = False) -> dict | None:
    """Busca metadados no IGDB (uma vez; repete só depois de 30 dias ou com forcar)."""
    cli = cliente_igdb(con)
    if not cli:
        return None
    m = con.execute("SELECT buscado_em FROM metadados WHERE jogo_id=?", (jogo_id,)).fetchone()
    if m and not forcar and time.time() - (m[0] or 0) < REPETIR_META:
        return None
    j = con.execute("SELECT j.titulo, p.igdb_id, j.capa_fonte FROM jogos j JOIN plataformas p ON p.id=j.plataforma_id "
                    "WHERE j.id=?", (jogo_id,)).fetchone()
    if not j:
        return None
    titulo, plat, capa_fonte = j
    try:
        achado = cli.procurar(titulo, plat)
    except Exception as e:  # noqa: BLE001
        log.warning("IGDB falhou para %s: %s", titulo, e)
        return None
    agora = time.time()
    if not achado:
        con.execute("INSERT INTO metadados(jogo_id, fonte, buscado_em) VALUES(?, 'igdb', ?) "
                    "ON CONFLICT(jogo_id) DO UPDATE SET buscado_em=excluded.buscado_em", (jogo_id, agora))
        con.commit()
        return None
    g, incerto = achado
    d = igdb_mod.IGDB.resumir(g)
    con.execute(
        "INSERT INTO metadados(jogo_id, fonte, externo_id, nome_fonte, sinopse, generos, ano, nota, incerto, buscado_em) "
        "VALUES(?, 'igdb', ?,?,?,?,?,?,?,?) ON CONFLICT(jogo_id) DO UPDATE SET fonte='igdb', "
        "externo_id=excluded.externo_id, nome_fonte=excluded.nome_fonte, sinopse=excluded.sinopse, "
        "generos=excluded.generos, ano=excluded.ano, nota=excluded.nota, incerto=excluded.incerto, "
        "buscado_em=excluded.buscado_em",
        (jogo_id, d["externo_id"], d["nome_fonte"], d["sinopse"], d["generos"], d["ano"], d["nota"], int(incerto), agora))
    if d["capa"] and capa_fonte is None:  # libretro tem prioridade; IGDB só cobre quem não tem capa
        con.execute("UPDATE jogos SET capa_fonte='igdb', capa_ref=?, capa_incerta=? WHERE id=?",
                    (d["capa"], int(incerto), jogo_id))
    con.commit()
    return d


# ---------------------------------------------------------------------------------------------- arquivo

LARGURA = 480  # suficiente para o card (2x em tela retina) e para a página do jogo


def caminho_capa(jogo_id: int, fonte: str) -> str:
    return os.path.join(config.PASTA_CAPAS, f"{jogo_id // 1000:03d}", f"{jogo_id}-{fonte}.jpg")


def reduzir(dados: bytes) -> bytes:
    """PNG do libretro (~500 KB) → JPEG 480 px (~50 KB). Sem Pillow, guarda como veio."""
    try:
        import io
        from PIL import Image
    except ImportError:
        return dados
    im = Image.open(io.BytesIO(dados))
    im = im.convert("RGB")
    if im.width > LARGURA:
        im = im.resize((LARGURA, round(im.height * LARGURA / im.width)), Image.LANCZOS)
    saida = io.BytesIO()
    im.save(saida, "JPEG", quality=84, optimize=True, progressive=True)
    return saida.getvalue()


def obter(con, jogo_id: int, baixar=libretro._get) -> str | None:
    """Caminho local da capa; baixa na primeira vez. None = sem capa (use o placeholder)."""
    with _trava(jogo_id):
        r = con.execute("SELECT j.capa_fonte, j.capa_ref, j.capa_buscada_em, p.libretro_repo FROM jogos j "
                        "JOIN plataformas p ON p.id=j.plataforma_id WHERE j.id=?", (jogo_id,)).fetchone()
        if not r:
            return None
        fonte, ref, buscada, repo = r
        if fonte and ref:
            arq = caminho_capa(jogo_id, fonte)
            if os.path.exists(arq):
                return arq
        if buscada and time.time() - buscada < REPETIR_FALHA and not (fonte and ref):
            return None
        if not (fonte and ref):  # tenta o IGDB (que também traz a sinopse)
            enriquecer(con, jogo_id)
            fonte, ref = con.execute("SELECT capa_fonte, capa_ref FROM jogos WHERE id=?", (jogo_id,)).fetchone()
        if fonte and ref:
            url = libretro.url_capa(repo, ref) if fonte == "libretro" else igdb_mod.url_capa(ref)
            arq = caminho_capa(jogo_id, fonte)
            try:
                dados = reduzir(baixar(url))
                os.makedirs(os.path.dirname(arq), exist_ok=True)
                with open(arq + ".tmp", "wb") as f:
                    f.write(dados)
                os.replace(arq + ".tmp", arq)
                con.execute("UPDATE jogos SET capa_buscada_em=? WHERE id=?", (time.time(), jogo_id))
                con.commit()
                return arq
            except Exception as e:  # noqa: BLE001
                log.warning("capa %s (%s) falhou: %s", jogo_id, url, e)
                if fonte == "libretro":  # nome casado não existe mais: deixa o IGDB tentar na próxima
                    con.execute("UPDATE jogos SET capa_fonte=NULL, capa_ref=NULL WHERE id=?", (jogo_id,))
        con.execute("UPDATE jogos SET capa_buscada_em=? WHERE id=?", (time.time(), jogo_id))
        con.commit()
        return None


def placeholder(titulo: str, plataforma: str) -> str:
    """Capa 3:4 sem imagem, no estilo do painel: fundo grafite, título em Archivo.
    O selo amarelo da plataforma fica no HTML (.selo), por cima desta imagem; não repetir aqui."""
    palavras, linhas, atual = titulo.upper().split(), [], ""
    for p in palavras:
        if len(atual) + len(p) + 1 > 14 and atual:
            linhas.append(atual)
            atual = p
        else:
            atual = f"{atual} {p}".strip()
    linhas.append(atual)
    linhas = linhas[:5]
    texto = "".join(f'<tspan x="20" dy="{0 if i == 0 else 34}">{html.escape(l)}</tspan>' for i, l in enumerate(linhas))
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 400" width="300" height="400">
<rect width="300" height="400" fill="#141414"/>
<path d="M0 0H300V400H0Z" fill="none" stroke="#262626" stroke-width="2"/>
<g stroke="#1c1c1c">{"".join(f'<path d="M0 {y}H300"/>' for y in range(20, 400, 20))}</g>
<text y="{230 - 17 * len(linhas)}" font-family="Archivo, Helvetica Neue, Arial, sans-serif" font-weight="800"
 font-stretch="75%" font-size="30" fill="#e8e6e1">{texto}</text>
<text x="280" y="370" text-anchor="end" font-family="JetBrains Mono, monospace" font-size="11" fill="#75726c">SEM CAPA</text>
</svg>'''


def enriquecer_lote(con, plataforma: str | None = None, limite: int | None = None, progresso=None) -> dict:
    """Metadados do IGDB para os jogos que ainda não têm (respeita o limite de 4 req/s do cliente)."""
    sql = ("SELECT j.id FROM jogos j JOIN plataformas p ON p.id=j.plataforma_id LEFT JOIN metadados m ON m.jogo_id=j.id "
           "WHERE j.qtd_versoes>0 AND m.jogo_id IS NULL")
    params: list = []
    if plataforma:
        sql += " AND p.curto=?"
        params.append(plataforma)
    sql += " ORDER BY j.so_extras, j.titulo" + (f" LIMIT {int(limite)}" if limite else "")
    ids = [r[0] for r in con.execute(sql, params)]
    t0, com = time.time(), 0
    for n, jid in enumerate(ids, 1):
        com += enriquecer(con, jid) is not None
        if progresso:
            progresso(n, len(ids))
    r = {"jogos": len(ids), "com_dados": com, "segundos": round(time.time() - t0)}
    log.info("IGDB lote %s: %s", plataforma or "todas", r)
    return r


if __name__ == "__main__":  # python -m app.meta.capas X360
    import sys
    from ..indexer import store as _store
    logging.basicConfig(level=logging.INFO)
    print(enriquecer_lote(_store.conectar(config.BANCO), sys.argv[1] if len(sys.argv) > 1 else None))
