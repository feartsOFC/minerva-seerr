"""Cliente mínimo da Web API v2 do qBittorrent (5.x; 4.x também: start/stop caem em resume/pause)."""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.cookiejar import CookieJar


class ErroQbit(RuntimeError):
    pass


class Qbit:
    def __init__(self, url: str, usuario: str = "", senha: str = "", timeout: int = 30):
        self.url = url.rstrip("/") + "/api/v2/"
        self.usuario, self.senha, self.timeout = usuario, senha, timeout
        self._abrir = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar())).open
        self._logado = False

    # -------------------------------------------------------------------------------- transporte
    def _req(self, caminho, dados=None, arquivos=None, tentar_login=True):
        cab = {"Referer": self.url.rsplit("/api/", 1)[0]}
        corpo = None
        if arquivos:
            fronteira = uuid.uuid4().hex
            partes = []
            for k, v in (dados or {}).items():
                partes.append(f'--{fronteira}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
            for nome_campo, (nome_arq, conteudo) in arquivos.items():
                partes.append(f'--{fronteira}\r\nContent-Disposition: form-data; name="{nome_campo}"; '
                              f'filename="{nome_arq}"\r\nContent-Type: application/x-bittorrent\r\n\r\n'.encode()
                              + conteudo + b"\r\n")
            corpo = b"".join(partes) + f"--{fronteira}--\r\n".encode()
            cab["Content-Type"] = f"multipart/form-data; boundary={fronteira}"
        elif dados is not None:
            corpo = urllib.parse.urlencode(dados).encode()
        try:
            with self._abrir(urllib.request.Request(self.url + caminho, data=corpo, headers=cab), timeout=self.timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 403 and tentar_login and self.usuario:
                self.login()
                return self._req(caminho, dados, arquivos, tentar_login=False)
            raise ErroQbit(f"qBittorrent {caminho}: HTTP {e.code} {e.read()[:200]!r}") from e
        except urllib.error.URLError as e:
            raise ErroQbit(f"qBittorrent fora do ar ({e.reason})") from e

    def _json(self, caminho, dados=None):
        return json.loads(self._req(caminho, dados))

    def login(self):
        r = self._req("auth/login", {"username": self.usuario, "password": self.senha}, tentar_login=False)
        if r.strip() != b"Ok.":
            raise ErroQbit("qBittorrent recusou o usuário/senha")
        self._logado = True

    # -------------------------------------------------------------------------------- consultas
    def versao(self) -> dict:
        b = self._json("app/buildInfo")
        return {"qbittorrent": self._req("app/version").decode().strip(), "webapi": self._req("app/webapiVersion").decode(),
                "libtorrent": b.get("libtorrent"), "lt20": str(b.get("libtorrent", "")).startswith("2.")}

    def espaco_livre(self) -> int | None:
        return self._json("sync/maindata").get("server_state", {}).get("free_space_on_disk")

    def info(self, hashes=None, tag=None) -> list:
        q = {}
        if hashes:
            q["hashes"] = "|".join(hashes) if isinstance(hashes, (list, tuple, set)) else hashes
        if tag:
            q["tag"] = tag
        return self._json("torrents/info?" + urllib.parse.urlencode(q))

    def torrent(self, infohash: str) -> dict | None:
        r = self.info(infohash)
        return r[0] if r else None

    def arquivos(self, infohash: str) -> list:
        return self._json(f"torrents/files?hash={infohash}")

    # -------------------------------------------------------------------------------- ações
    def adicionar_torrent(self, conteudo: bytes, nome: str, categoria: str, tags: str, savepath: str | None = None):
        dados = {"category": categoria, "tags": tags, "stopped": "true", "paused": "true",
                 "contentLayout": "Original", "autoTMM": "false" if savepath else "true"}
        if savepath:
            dados["savepath"] = savepath
        r = self._req("torrents/add", dados, {"torrents": (nome, conteudo)})
        if r.strip() not in (b"Ok.", b""):
            raise ErroQbit(f"qBittorrent recusou o torrent: {r[:200]!r}")

    def prioridade(self, infohash: str, ids, prio: int):
        ids = list(ids)
        for i in range(0, len(ids), 2000):  # listas enormes (Xbox 360: 4.459 arquivos) em lotes
            self._req("torrents/filePrio", {"hash": infohash, "id": "|".join(map(str, ids[i:i + 2000])), "priority": prio})

    def iniciar(self, infohash: str):
        try:
            self._req("torrents/start", {"hashes": infohash})
        except ErroQbit:
            self._req("torrents/resume", {"hashes": infohash})

    def apagar(self, infohash: str, com_arquivos: bool = True):
        self._req("torrents/delete", {"hashes": infohash, "deleteFiles": "true" if com_arquivos else "false"})

    def criar_categoria(self, nome: str, pasta: str = ""):
        try:
            self._req("torrents/createCategory", {"category": nome, "savePath": pasta})
        except ErroQbit:
            pass  # já existe

    def esperar_torrent(self, infohash: str, segundos: int = 30) -> dict:
        fim = time.time() + segundos
        while time.time() < fim:
            t = self.torrent(infohash)
            if t and t.get("state") not in ("metaDL", "checkingResumeData"):
                return t
            time.sleep(0.5)
        raise ErroQbit("o torrent não apareceu no qBittorrent a tempo")


def indice_por_caminho(arquivos_qbit: list, caminho: str) -> int | None:
    """Acha o índice do qBit pelo caminho (o qBit põe a pasta-raiz "Minerva_Myrient/" na frente e pode esconder pads,
    então o número do so= não serve como id)."""
    for f in arquivos_qbit:
        nome = f["name"].replace("\\", "/")
        if nome == caminho or nome.endswith("/" + caminho):
            return f["index"]
    return None
