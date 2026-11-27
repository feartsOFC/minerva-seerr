"""Teste da API (precisa de fastapi + httpx; roda no container)."""
import os
import tempfile
import unittest

try:
    from fastapi.testclient import TestClient
except ImportError:  # host sem FastAPI
    TestClient = None


@unittest.skipIf(TestClient is None, "fastapi não instalado — rode dentro do container")
class TestAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app import config
        from app.indexer import store
        from app.indexer.torrent import ler_torrent, nome_colecao
        from tests.util import ENTRADAS_COM_PADS, torrent_sintetico

        cls.tmp = tempfile.TemporaryDirectory()
        config.BANCO = os.path.join(cls.tmp.name, "t.db")
        c = store.conectar(config.BANCO)
        dados, cls.hash = torrent_sintetico(ENTRADAS_COM_PADS)
        url = "https://cdn.example/Minerva_Myrient%20-%20Redump%20-%20Microsoft%20-%20Xbox%20360.torrent"
        cls.cid = store.gravar_colecao(c, url, nome_colecao(url), "/x", ler_torrent(dados))
        c.commit()
        c.close()
        from app.main import app
        cls.cli = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_busca_e_magnet(self):
        r = self.cli.get("/api/busca", params={"q": "guitar hero rev"}).json()
        self.assertEqual([i["nome"] for i in r["itens"]], ["Guitar Hero III - Legends of Rock (USA) (Rev 1).zip"])
        d = self.cli.get(f"/api/arquivos/{self.cid}/4").json()
        self.assertIn(f"btih:{self.hash}", d["magnet"])
        self.assertIn("&so=4", d["magnet"])
        self.assertEqual(self.cli.get(f"/api/arquivos/{self.cid}/1").status_code, 404)

    def test_saude(self):
        self.assertEqual(self.cli.get("/api/saude").json()["arquivos"], 4)
