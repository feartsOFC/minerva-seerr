"""Bencode (formato dos .torrent).

`bdecode` guarda o trecho exato de bytes do dicionário `info` — o infohash v1 é o SHA-1
desses bytes como estão no arquivo, não de uma recodificação.
"""


class BencodeError(ValueError):
    pass


def bdecode(dados: bytes, spans: dict | None = None):
    """Decodifica bencode. Se `spans` for passado, grava spans[b"info"] = (início, fim)
    do valor da chave `info` do dicionário de topo."""

    def decodificar(i: int, topo: bool = False):
        c = dados[i:i + 1]
        if c == b"i":
            fim = dados.index(b"e", i)
            return int(dados[i + 1:fim]), fim + 1
        if c == b"l":
            i += 1
            lista = []
            while dados[i:i + 1] != b"e":
                if i >= len(dados):
                    raise BencodeError("lista sem fim")
                v, i = decodificar(i)
                lista.append(v)
            return lista, i + 1
        if c == b"d":
            i += 1
            dic = {}
            while dados[i:i + 1] != b"e":
                if i >= len(dados):
                    raise BencodeError("dicionário sem fim")
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
            if ini + tam > len(dados):
                raise BencodeError("string maior que o arquivo")
            return dados[ini:ini + tam], ini + tam
        raise BencodeError(f"bencode inválido na posição {i}")

    try:
        valor, fim = decodificar(0, topo=True)
    except (ValueError, IndexError) as e:
        if isinstance(e, BencodeError):
            raise
        raise BencodeError(str(e)) from e
    return valor


def bencode(valor) -> bytes:
    """Codifica em bencode (chaves de dicionário em ordem, como manda a BEP 3)."""
    if isinstance(valor, bool):
        raise TypeError("bool não existe em bencode")
    if isinstance(valor, int):
        return b"i%de" % valor
    if isinstance(valor, str):
        valor = valor.encode()
    if isinstance(valor, bytes):
        return b"%d:%s" % (len(valor), valor)
    if isinstance(valor, list):
        return b"l" + b"".join(bencode(v) for v in valor) + b"e"
    if isinstance(valor, dict):
        itens = sorted((k.encode() if isinstance(k, str) else k, v) for k, v in valor.items())
        return b"d" + b"".join(bencode(k) + bencode(v) for k, v in itens) + b"e"
    raise TypeError(f"tipo sem bencode: {type(valor)}")
