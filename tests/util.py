"""Torrents sintéticos para os testes."""
import hashlib

from app.indexer.bencode import bencode

PECA = 16384


def torrent_sintetico(entradas, announce="udp://a.example:1337/announce",
                      announce_list=(("udp://a.example:1337/announce",), ("http://b.example/announce",))):
    """entradas = [(caminho_lista, tamanho, attr_ou_None), ...] na ordem de info.files."""
    files = []
    for partes, tam, attr in entradas:
        f = {"length": tam, "path": list(partes)}
        if attr:
            f["attr"] = attr
        files.append(f)
    info = {"name": "Minerva_Myrient", "piece length": PECA, "pieces": b"\x00" * 20, "files": files}
    meta = {"announce": announce, "announce-list": [list(c) for c in announce_list], "info": info}
    dados = bencode(meta)
    return dados, hashlib.sha1(bencode(info)).hexdigest()


# Coleção com 3 jogos e pads entre eles (BEP 47) + pads no estilo antigo:
#   idx 0 Halo 3 (USA)  | 1 pad attr=p | 2 Halo 3 (Japan) | 3 .pad/123 | 4 Guitar Hero III (USA) (Rev 1)
#   5 _____padding_file_0 | 6 Guitar Hero III (USA)
ENTRADAS_COM_PADS = [
    (["Redump", "Microsoft - Xbox 360", "Halo 3 (USA, Europe).zip"], 1000, None),
    ([".pad", "15384"], 15384, b"p"),
    (["Redump", "Microsoft - Xbox 360", "Halo 3 (Japan).zip"], 2000, None),
    ([".pad", "14384"], 14384, None),
    (["Redump", "Microsoft - Xbox 360", "Guitar Hero III - Legends of Rock (USA) (Rev 1).zip"], 3000, None),
    (["_____padding_file_0_if you see this file, please update to BitComet 0.85 or above____"], 13384, None),
    (["Redump", "Microsoft - Xbox 360", "Guitar Hero III - Legends of Rock (USA).zip"], 4000, None),
]
