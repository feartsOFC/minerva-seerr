"""Magnet que baixa só um arquivo (BEP 53, parâmetro so=)."""
import urllib.parse


def montar_magnet(infohash: str, indice: int | None, trackers=(), nome_dn: str = "Minerva_Myrient") -> str:
    if len(infohash) != 40 or any(c not in "0123456789abcdef" for c in infohash.lower()):
        raise ValueError(f"infohash v1 inválido: {infohash!r}")
    partes = [f"xt=urn:btih:{infohash.lower()}", "dn=" + urllib.parse.quote(nome_dn, safe="")]
    if indice is not None:
        if indice < 0:
            raise ValueError("so= não pode ser negativo")
        partes.append(f"so={indice}")
    partes += ["tr=" + urllib.parse.quote(t, safe="") for t in trackers]
    return "magnet:?" + "&".join(partes)


def limitar_trackers(trackers: list, quantidade: int | None) -> list:
    """quantidade 0/None = todos."""
    return list(trackers) if not quantidade else list(trackers)[:quantidade]


def ler_magnet(magnet: str) -> dict:
    """Decompõe um magnet (útil nos testes e para conferir o que foi enviado)."""
    if not magnet.startswith("magnet:?"):
        raise ValueError("não é magnet")
    q = urllib.parse.parse_qs(magnet[8:], keep_blank_values=True)
    xt = q.get("xt", [""])[0]
    return {
        "infohash": xt.rsplit(":", 1)[-1].lower(),
        "dn": q.get("dn", [None])[0],
        "so": [int(x) for x in ",".join(q.get("so", [])).split(",") if x != ""] if q.get("so") else [],
        "tr": q.get("tr", []),
    }
