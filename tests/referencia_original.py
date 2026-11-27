"""Cópia literal das funções de parsing/magnet do minerva_index.py original (validado contra o site do
Minerva). Serve de referência: o módulo novo precisa gerar exatamente o mesmo magnet."""
import hashlib
import urllib.parse


def bdecode(dados: bytes, spans: dict = None):
    def decodificar(i, topo=False):
        c = dados[i:i + 1]
        if c == b"i":
            fim = dados.index(b"e", i)
            return int(dados[i + 1:fim]), fim + 1
        if c == b"l":
            i += 1
            lista = []
            while dados[i:i + 1] != b"e":
                v, i = decodificar(i)
                lista.append(v)
            return lista, i + 1
        if c == b"d":
            i += 1
            dic = {}
            while dados[i:i + 1] != b"e":
                k, i = decodificar(i)
                ini_v = i
                v, i = decodificar(i)
                if topo and spans is not None and k == b"info":
                    spans[b"info"] = (ini_v, i)
                dic[k] = v
            return dic, i + 1
        if c.isdigit():
            sep = dados.index(b":", i)
            tam = int(dados[i:sep])
            ini = sep + 1
            return dados[ini:ini + tam], ini + tam
        raise ValueError(f"bencode inválido na posição {i}")

    valor, _ = decodificar(0, topo=True)
    return valor


def _txt(b) -> str:
    if isinstance(b, bytes):
        return b.decode("utf-8", errors="replace")
    return str(b)


def listar_arquivos_torrent(conteudo: bytes):
    spans = {}
    meta = bdecode(conteudo, spans)
    info = meta[b"info"]
    ini, fim = spans[b"info"]
    infohash = hashlib.sha1(conteudo[ini:fim]).hexdigest()
    nome = _txt(info.get(b"name.utf-8", info.get(b"name", b"")))
    trackers = []
    for camada in meta.get(b"announce-list", []) or []:
        for t in camada:
            t = _txt(t)
            if t not in trackers:
                trackers.append(t)
    if b"announce" in meta and _txt(meta[b"announce"]) not in trackers:
        trackers.insert(0, _txt(meta[b"announce"]))
    arquivos = []
    if b"files" in info:
        for idx, f in enumerate(info[b"files"]):
            partes = f.get(b"path.utf-8", f.get(b"path", []))
            caminho = "/".join(_txt(p) for p in partes)
            if "_____padding_file" in caminho or caminho.startswith(".pad/") \
                    or b"p" in f.get(b"attr", b""):
                continue
            arquivos.append((idx, caminho, int(f.get(b"length", 0))))
    else:
        arquivos.append((0, nome, int(info.get(b"length", 0))))
    return {"nome": nome, "infohash": infohash, "trackers": trackers, "arquivos": arquivos}


def montar_magnet(infohash: str, indice: int, trackers, nome_dn: str = "Minerva_Myrient") -> str:
    partes = [f"xt=urn:btih:{infohash}", "dn=" + urllib.parse.quote(nome_dn, safe=""), f"so={indice}"]
    partes += ["tr=" + urllib.parse.quote(t, safe="") for t in trackers]
    return "magnet:?" + "&".join(partes)
