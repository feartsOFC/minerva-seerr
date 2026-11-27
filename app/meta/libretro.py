"""Capas do libretro-thumbnails (repositórios no GitHub; thumbnails.libretro.com está vazio desde 11/2026).

As capas usam o nome exato No-Intro/Redump, então o casamento costuma ser direto. Ordem de tentativa:
  1. nome exato de alguma versão do jogo (na ordem de preferência)        → certo
  2. mesmo título-base em outra região/revisão                            → certo (arte de outra região)
  3. título parecido (difflib ≥ 0,9)                                      → incerto
A lista de capas de cada sistema é baixada uma vez (API do GitHub) e guardada por 7 dias.
"""
import difflib
import json
import os
import re
import time
import urllib.parse
import urllib.request

from ..catalog.titles import analisar, chave_titulo, peso_versao

API = "https://api.github.com/repos/libretro-thumbnails/{repo}/git/trees/master?recursive=1"
RAW = "https://raw.githubusercontent.com/libretro-thumbnails/{repo}/master/Named_Boxarts/{nome}.png"
UA = "minerva-seerr/0.1 (capas)"
VALIDADE = 7 * 86400
_cache_mem: dict = {}


def sanitizar(nome: str) -> str:
    """Regra do libretro para nome de arquivo: &*/:`<>?\\| viram _."""
    return re.sub(r'[&*/:`<>?\\|"]', "_", nome)


def _get(url: str, timeout: int = 30) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=timeout) as r:
        return r.read()


def lista(repo: str, pasta_cache: str, baixar=_get) -> list[str]:
    """Nomes (sem .png) das capas do sistema. Cache em disco; em falha de rede usa o cache velho."""
    if repo in _cache_mem and time.time() - _cache_mem[repo][0] < VALIDADE:
        return _cache_mem[repo][1]
    os.makedirs(pasta_cache, exist_ok=True)
    arq = os.path.join(pasta_cache, f"libretro-{repo}.json")
    nomes = None
    if os.path.exists(arq) and time.time() - os.path.getmtime(arq) < VALIDADE:
        with open(arq, encoding="utf-8") as f:
            nomes = json.load(f)
    if nomes is None:
        try:
            arvore = json.loads(baixar(API.format(repo=repo)))
            nomes = sorted(p["path"][len("Named_Boxarts/"):-4] for p in arvore.get("tree", [])
                           if p["path"].startswith("Named_Boxarts/") and p["path"].endswith(".png"))
            with open(arq + ".tmp", "w", encoding="utf-8") as f:
                json.dump(nomes, f)
            os.replace(arq + ".tmp", arq)
        except Exception:  # noqa: BLE001 — sem rede: fica com o que tiver
            nomes = json.load(open(arq, encoding="utf-8")) if os.path.exists(arq) else []
    _cache_mem[repo] = (time.time(), nomes)
    return nomes


_ROMANOS = {"ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7", "viii": "8", "ix": "9", "x": "10",
            "xi": "11", "xii": "12", "xiii": "13"}


def numeros(chave: str) -> set:
    """Números de continuação/edição na chave ("winning eleven 10" → {"10"}; "final fantasy x" → {"10"})."""
    r = set()
    for t in chave.split():
        t = re.sub(r"(st|nd|rd|th)$", "", t) if re.match(r"^\d+(st|nd|rd|th)$", t) else t
        if t.isdigit():
            r.add(str(int(t)))
        elif t in _ROMANOS:
            r.add(_ROMANOS[t])
    return r


class Indice:
    """Índice de casamento de um sistema."""

    def __init__(self, nomes: list[str]):
        self.exatos = set(nomes)
        self.por_chave: dict = {}
        for n in nomes:
            self.por_chave.setdefault(chave_titulo(analisar(n).titulo), []).append(n)
        self.chaves = list(self.por_chave)

    def casar(self, nomes_versoes: list[str]) -> tuple[str, bool] | None:
        """nomes_versoes = nomes de arquivo do jogo (qualquer ordem). Devolve (nome_libretro, incerto)."""
        ordenados = sorted(nomes_versoes, key=lambda n: peso_versao(analisar(n)))
        for n in ordenados:
            alvo = sanitizar(re.sub(r"\.(zip|7z)$", "", n, flags=re.I))
            if alvo in self.exatos:
                return alvo, False
        chave = chave_titulo(analisar(ordenados[0]).titulo)
        if chave in self.por_chave:
            return min(self.por_chave[chave], key=lambda n: peso_versao(analisar(n))), False
        nums = numeros(chave)
        for perto in difflib.get_close_matches(chave, self.chaves, n=3, cutoff=0.9):
            if numeros(perto) == nums:  # "Tales of Destiny" nunca casa com "Tales of Destiny 2"
                return min(self.por_chave[perto], key=lambda n: peso_versao(analisar(n))), True
        return None


def url_capa(repo: str, nome: str) -> str:
    return RAW.format(repo=repo, nome=urllib.parse.quote(nome))
