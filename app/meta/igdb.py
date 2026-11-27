"""IGDB (credenciais da Twitch, grátis): sinopse, gêneros, ano, nota e capa.

Token "client credentials" renovado sozinho (dura ~60 dias; renova 1 dia antes ou ao receber 401).
Limite da API: 4 requisições/s — respeitado com um intervalo mínimo entre chamadas.
"""
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from ..catalog.titles import chave_titulo

TOKEN_URL = "https://id.twitch.tv/oauth2/token"
API = "https://api.igdb.com/v4/"
CAPA = "https://images.igdb.com/igdb/image/upload/t_cover_big/{image_id}.jpg"
UA = "minerva-seerr/0.1"


class ErroIGDB(RuntimeError):
    pass


class IGDB:
    def __init__(self, client_id: str, client_secret: str, http=None):
        self.client_id, self.client_secret = client_id.strip(), client_secret.strip()
        self._token, self._expira = None, 0.0
        self._trava = threading.Lock()
        self._ultima = 0.0
        self._http = http or self._http_padrao

    @staticmethod
    def _http_padrao(url, dados=None, cab=None):
        req = urllib.request.Request(url, data=dados, headers={"User-Agent": UA, **(cab or {})},
                                     method="POST" if dados is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def token(self, forcar=False) -> str:
        if not forcar and self._token and time.time() < self._expira - 86400:
            return self._token
        q = urllib.parse.urlencode({"client_id": self.client_id, "client_secret": self.client_secret,
                                    "grant_type": "client_credentials"})
        st, corpo = self._http(f"{TOKEN_URL}?{q}", b"")
        if st != 200:
            raise ErroIGDB(f"Twitch recusou as credenciais (HTTP {st})")
        d = json.loads(corpo)
        self._token, self._expira = d["access_token"], time.time() + int(d.get("expires_in", 0))
        return self._token

    def consulta(self, endpoint: str, corpo: str):
        with self._trava:
            espera = 0.26 - (time.time() - self._ultima)
            if espera > 0:
                time.sleep(espera)
            self._ultima = time.time()
        for tentativa in (0, 1):
            cab = {"Client-ID": self.client_id, "Authorization": f"Bearer {self.token(forcar=tentativa == 1)}",
                   "Accept": "application/json"}
            st, dados = self._http(API + endpoint, corpo.encode(), cab)
            if st == 401 and tentativa == 0:
                continue
            if st == 429:
                time.sleep(1)
                continue
            if st != 200:
                raise ErroIGDB(f"IGDB {endpoint}: HTTP {st}")
            return json.loads(dados)
        raise ErroIGDB("IGDB: token recusado")

    def validar(self) -> dict:
        self.token(forcar=True)
        self.consulta("platforms", "fields name; where id = 12; limit 1;")
        return {"ok": True}

    def procurar(self, titulo: str, plataforma_id: int | None):
        """Melhor jogo para o título; devolve (dict, incerto) ou None."""
        busca = re.sub(r'["\\]', " ", titulo.replace(" - ", ": "))
        campos = ("fields name, summary, first_release_date, total_rating, aggregated_rating, rating, "
                  "genres.name, cover.image_id, platforms;")
        filtro = f" where platforms = ({plataforma_id});" if plataforma_id else ""
        r = self.consulta("games", f'search "{busca}"; {campos}{filtro} limit 10;')
        if not r and plataforma_id:  # às vezes o jogo não está marcado na plataforma
            r = self.consulta("games", f'search "{busca}"; {campos} limit 10;')
            incerto_base = True
        else:
            incerto_base = False
        if not r:
            return None
        alvo = chave_titulo(titulo)
        exato = [g for g in r if chave_titulo(g.get("name", "")) == alvo]
        g = exato[0] if exato else r[0]
        return g, (incerto_base or not exato)

    @staticmethod
    def resumir(g: dict) -> dict:
        ano = time.gmtime(g["first_release_date"]).tm_year if g.get("first_release_date") else None
        nota = g.get("total_rating") or g.get("aggregated_rating") or g.get("rating")
        return {"externo_id": str(g["id"]), "nome_fonte": g.get("name"), "sinopse": g.get("summary"),
                "generos": ", ".join(x["name"] for x in g.get("genres", []) if "name" in x) or None,
                "ano": ano, "nota": round(nota / 10, 1) if nota else None,
                "capa": (g.get("cover") or {}).get("image_id")}


def url_capa(image_id: str) -> str:
    return CAPA.format(image_id=image_id)
