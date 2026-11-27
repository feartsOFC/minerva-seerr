"""Varredura do índice do Minerva, download dos .torrent e reindexação."""
import email.utils
import html
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import store
from .torrent import ler_torrent, nome_colecao

INDICE_PADRAO = "https://cdn.minerva-archive.org/"
USER_AGENT = "minerva-seerr/0.1 (indexador self-hosted)"
HREF_RE = re.compile(r'href\s*=\s*"([^"]+)"', re.IGNORECASE)
log = logging.getLogger("minerva.indexer")


def baixar(url: str, desde: float | None = None, tentativas: int = 4) -> bytes | None:
    """GET com retentativa. Com `desde` (mtime local), manda If-Modified-Since e devolve None em 304."""
    ultimo = None
    for n in range(tentativas):
        try:
            cab = {"User-Agent": USER_AGENT}
            if desde:
                cab["If-Modified-Since"] = email.utils.formatdate(desde, usegmt=True)
            with urllib.request.urlopen(urllib.request.Request(url, headers=cab), timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 304:
                return None
            if e.code in (403, 404, 410):
                raise RuntimeError(f"{url}: HTTP {e.code}") from e
            ultimo = e
        except Exception as e:  # noqa: BLE001
            ultimo = e
        time.sleep(2 ** n)
    raise RuntimeError(f"falha ao baixar {url}: {ultimo}")


def extrair_torrents(pagina: str, url_base: str) -> list[str]:
    """Links .torrent de uma listagem de diretório (relativos ou absolutos), só do mesmo host/prefixo."""
    host = urllib.parse.urlparse(url_base).netloc
    achados = set()
    for href in HREF_RE.findall(pagina):
        href = html.unescape(href)
        if href.startswith(("?", "#", "../")) or href in ("/", "./"):
            continue
        absoluto = urllib.parse.urljoin(url_base, href)
        alvo = urllib.parse.urlparse(absoluto)
        if alvo.netloc == host and absoluto.startswith(url_base) and alvo.path.lower().endswith(".torrent"):
            achados.add(absoluto)
    return sorted(achados)


def varrer_indice(url_base: str = INDICE_PADRAO) -> list[str]:
    base = url_base if url_base.endswith("/") else url_base + "/"
    return extrair_torrents(baixar(base).decode("utf-8", errors="replace"), base)


def filtrar(urls: list[str], nomes: list[str] | None) -> list[str]:
    """`nomes` = lista exata de coleções (ex.: MVP). None/vazio = todas."""
    if not nomes:
        return urls
    alvo = {n.casefold() for n in nomes}
    return [u for u in urls if nome_colecao(u).casefold() in alvo]


def arquivo_local(pasta: str, url: str) -> str:
    return os.path.join(pasta, re.sub(r'[<>:"\\|?*/]', "_", urllib.parse.unquote(url.rsplit("/", 1)[-1])))


def atualizar(con, pasta_torrents: str, url_base: str = INDICE_PADRAO, colecoes: list[str] | None = None,
              conexoes: int = 4, forcar: bool = False, progresso=None) -> dict:
    """Atualiza o índice. Só reindexa coleção cujo .torrent mudou (ou nova). Remove as que sumiram
    do índice (ou saíram do filtro). Devolve um resumo."""
    progresso = progresso or (lambda etapa, n, total: None)
    os.makedirs(pasta_torrents, exist_ok=True)
    base = url_base if url_base.endswith("/") else url_base + "/"
    inicio = time.time()

    todas = varrer_indice(base)
    if not todas:
        raise RuntimeError("índice vazio — o endereço mudou?")
    urls = filtrar(todas, colecoes)
    conhecidas = {r["url"]: r for r in store.colecoes(con)}

    def obter(url):
        destino = arquivo_local(pasta_torrents, url)
        existe = os.path.exists(destino)
        if existe and not forcar:
            dados = baixar(url, desde=os.path.getmtime(destino))
            if dados is None:
                return url, destino, False
        else:
            dados = baixar(url)
        with open(destino + ".tmp", "wb") as f:
            f.write(dados)
        os.replace(destino + ".tmp", destino)
        return url, destino, True

    falhas, locais, novos = [], {}, 0
    with ThreadPoolExecutor(max_workers=conexoes) as ex:
        futuros = {ex.submit(obter, u): u for u in urls}
        for n, fut in enumerate(as_completed(futuros), 1):
            try:
                url, destino, mudou = fut.result()
                locais[url] = (destino, mudou)
                novos += mudou
            except Exception as e:  # noqa: BLE001
                falhas.append({"url": futuros[fut], "erro": str(e)})
            progresso("baixando", n, len(urls))

    reindexadas = 0
    for n, (url, (destino, mudou)) in enumerate(sorted(locais.items()), 1):
        conhecida = conhecidas.get(url)
        if conhecida and not mudou and not forcar and conhecida["infohash"] \
                and (conhecida["indexado_em"] or 0) >= os.path.getmtime(destino):
            if con.execute("SELECT 1 FROM arquivos WHERE colecao_id=? AND jogo_id IS NULL LIMIT 1",
                           (conhecida["id"],)).fetchone():
                from ..catalog import catalogo  # banco de antes do catálogo: monta os jogos sem rebaixar nada
                catalogo.reconstruir(con, conhecida["id"])
                con.commit()
            continue
        try:
            with open(destino, "rb") as f:
                t = ler_torrent(f.read())
            store.gravar_colecao(con, url, nome_colecao(url), destino, t)
            con.commit()
            reindexadas += 1
        except Exception as e:  # noqa: BLE001
            con.rollback()
            falhas.append({"url": url, "erro": f"erro ao ler torrent: {e}"})
        progresso("indexando", n, len(locais))

    # coleção que sumiu do índice ou saiu do filtro: sai do banco (o .torrent local fica)
    removidas = 0
    validas = set(urls)
    for url, r in conhecidas.items():
        if url not in validas:
            store.remover_colecao(con, r["id"])
            removidas += 1
    con.commit()

    resumo = {"no_indice": len(todas), "selecionadas": len(urls), "baixadas": novos, "reindexadas": reindexadas,
              "removidas": removidas, "falhas": falhas, "segundos": round(time.time() - inicio, 1)}
    store.definir_ajuste(con, "ultima_atualizacao", {"em": time.time(), **resumo})
    log.info("índice atualizado: %s", resumo)
    return resumo
