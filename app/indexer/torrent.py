"""Leitura de .torrent: infohash, trackers e lista de arquivos com o índice real do `so=`."""
import hashlib
import re
import urllib.parse
from dataclasses import dataclass, field

from .bencode import bdecode


@dataclass
class Arquivo:
    idx: int          # posição em info.files CONTANDO os pads — é o que o so= espera
    caminho: str      # ex.: "Redump/Microsoft - Xbox 360/Halo 3 (USA).zip"
    tamanho: int

    @property
    def nome(self) -> str:
        return self.caminho.rsplit("/", 1)[-1]

    @property
    def pasta(self) -> str:
        """Pasta do sistema dentro do torrent, ex.: "Microsoft - Xbox 360"."""
        partes = self.caminho.split("/")
        return partes[-2] if len(partes) >= 2 else ""


@dataclass
class Torrent:
    nome: str                     # info.name (no Minerva é sempre "Minerva_Myrient")
    infohash: str                 # v1, hex minúsculo
    trackers: list = field(default_factory=list)
    arquivos: list = field(default_factory=list)   # só arquivos reais (sem pads)
    total_entradas: int = 0       # tamanho de info.files, com pads


def _txt(b) -> str:
    return b.decode("utf-8", errors="replace") if isinstance(b, bytes) else str(b)


def eh_padding(entrada: dict, caminho: str) -> bool:
    """BEP 47: attr contém 'p'. Torrents antigos usam nomes ".pad/N" ou "_____padding_file"."""
    return (b"p" in entrada.get(b"attr", b"")
            or caminho.startswith(".pad/") or "/.pad/" in caminho
            or "_____padding_file" in caminho)


def ler_torrent(conteudo: bytes) -> Torrent:
    spans: dict = {}
    meta = bdecode(conteudo, spans)
    if not isinstance(meta, dict) or b"info" not in meta or b"info" not in spans:
        raise ValueError("torrent sem dicionário info")
    info = meta[b"info"]
    ini, fim = spans[b"info"]
    infohash = hashlib.sha1(conteudo[ini:fim]).hexdigest()
    nome = _txt(info.get(b"name.utf-8", info.get(b"name", b"")))

    # Mesma ordem do minerva_index.py (que gera magnet idêntico ao do site): announce-list sem
    # repetição; o announce só entra (no começo) se não estiver na lista.
    trackers: list = []
    for camada in meta.get(b"announce-list", []) or []:
        for t in camada:
            t = _txt(t)
            if t not in trackers:
                trackers.append(t)
    if b"announce" in meta and _txt(meta[b"announce"]) not in trackers:
        trackers.insert(0, _txt(meta[b"announce"]))

    arquivos, total = [], 0
    if b"files" in info:  # v1 multi-arquivo (ou híbrido: o so= segue info.files)
        total = len(info[b"files"])
        for idx, f in enumerate(info[b"files"]):
            partes = f.get(b"path.utf-8", f.get(b"path", []))
            caminho = "/".join(_txt(p) for p in partes)
            if eh_padding(f, caminho):
                continue
            arquivos.append(Arquivo(idx, caminho, int(f.get(b"length", 0))))
    elif b"file tree" in info:  # só v2: ordem da árvore
        def andar(arvore, prefixo):
            for k in sorted(arvore):
                v = arvore[k]
                if k == b"":
                    arquivos.append(Arquivo(len(arquivos), "/".join(prefixo), int(v.get(b"length", 0))))
                else:
                    andar(v, prefixo + [_txt(k)])
        andar(info[b"file tree"], [])
        total = len(arquivos)
    else:  # arquivo único
        arquivos.append(Arquivo(0, nome, int(info.get(b"length", 0))))
        total = 1

    return Torrent(nome=nome, infohash=infohash, trackers=trackers, arquivos=arquivos, total_entradas=total)


def nome_colecao(url_ou_arquivo: str) -> str:
    """'…/Minerva_Myrient%20-%20Redump%20-%20Microsoft%20-%20Xbox%20360.torrent'
    → 'Redump - Microsoft - Xbox 360'. O nome interno (info.name) não serve: é sempre Minerva_Myrient."""
    n = urllib.parse.unquote(url_ou_arquivo.rsplit("/", 1)[-1])
    n = re.sub(r"\.torrent$", "", n, flags=re.IGNORECASE)
    return re.sub(r"^Minerva_Myrient\s*-\s*", "", n).strip()


def acervo_de(nome: str) -> str:
    """Primeira parte do nome da coleção: 'Redump', 'No-Intro', 'TOSEC'…"""
    return nome.split(" - ", 1)[0].strip()
