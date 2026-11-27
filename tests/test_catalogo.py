"""Catálogo montado a partir de um torrent sintético: jogos, versões, discos e plataforma."""
import unittest

from app.catalog import catalogo
from app.indexer import store
from app.indexer.torrent import ler_torrent, nome_colecao
from tests.util import torrent_sintetico

X = ["Redump", "Microsoft - Xbox 360"]
ENTRADAS = [
    (X + ["Halo 3 (USA, Europe).zip"], 100, None),
    ([".pad", "1"], 1, b"p"),
    (X + ["Halo 3 (Brazil).zip"], 110, None),
    (X + ["Halo 3 (Japan).zip"], 120, None),
    (X + ["Guitar Hero III - Legends of Rock (USA) (Rev 1).zip"], 200, None),
    (X + ["Guitar Hero III - Legends of Rock (USA) (Demo).zip"], 20, None),
    (X + ["Forza Motorsport 3 (USA) (Disc 1) (Game Disc).zip"], 300, None),
    (X + ["Forza Motorsport 3 (USA) (Disc 2) (Install Disc).zip"], 50, None),
    (X + ["Kinect Demo Disc (USA) (Demo).zip"], 10, None),
]
URL = "https://cdn.example/Minerva_Myrient%20-%20Redump%20-%20Microsoft%20-%20Xbox%20360.torrent"


class TestCatalogo(unittest.TestCase):
    def setUp(self):
        self.con = store.conectar(":memory:")
        dados, _ = torrent_sintetico(ENTRADAS)
        self.cid = store.gravar_colecao(self.con, URL, nome_colecao(URL), "/x", ler_torrent(dados))
        self.con.commit()

    def tearDown(self):
        self.con.close()

    def jogo_por_titulo(self, titulo):
        (jid,) = self.con.execute("SELECT id FROM jogos WHERE titulo=?", (titulo,)).fetchone()
        return catalogo.jogo(self.con, jid)

    def test_plataforma(self):
        self.assertEqual(catalogo.sistema_de("No-Intro - Nintendo - Nintendo 64 (BigEndian)"), "Nintendo - Nintendo 64")
        self.assertEqual(catalogo.sistema_de("No-Intro - Non-Redump - Sony - PlayStation 2"), "Sony - PlayStation 2")
        p = catalogo.plataformas(self.con)
        self.assertEqual([(x["nome"], x["curto"], x["jogos"]) for x in p], [("Xbox 360", "X360", 4)])

    def test_halo_um_card_tres_versoes_brasil_primeiro(self):
        j = self.jogo_por_titulo("Halo 3")
        self.assertEqual([v["rotulo"] for v in j["versoes"]], ["Brazil", "USA, Europe", "Japan"])
        self.assertEqual(j["principal"], j["versoes"][0]["chave"])
        self.assertEqual(j["tamanho_princ"], 110)

    def test_demo_marcada_e_rev(self):
        j = self.jogo_por_titulo("Guitar Hero III - Legends of Rock")
        self.assertEqual([(v["rotulo"], v["tipo"]) for v in j["versoes"]], [("USA · Rev 1", "jogo"), ("USA · Demo", "demo")])
        v = j["versoes"][0]["arquivos"][0]
        self.assertEqual((v["colecao_id"], v["idx"]), (self.cid, 4))

    def test_discos_numa_versao(self):
        j = self.jogo_por_titulo("Forza Motorsport 3")
        self.assertEqual(len(j["versoes"]), 1)
        self.assertEqual(j["versoes"][0]["tamanho"], 350)
        self.assertEqual([a["disco"] for a in j["versoes"][0]["arquivos"]], ["1 · Game Disc", "2 · Install Disc"])
        arqs = catalogo.arquivos_da_versao(self.con, j["id"], j["versoes"][0]["chave"])
        self.assertEqual([a["idx"] for a in arqs], [6, 7])

    def test_lista_busca_e_extras(self):
        nomes = lambda **kw: [x["titulo"] for x in catalogo.listar_jogos(self.con, **kw)]
        self.assertEqual(nomes(), ["Forza Motorsport 3", "Guitar Hero III - Legends of Rock", "Halo 3"])
        self.assertIn("Kinect Demo Disc", nomes(incluir_extras=True))
        self.assertEqual(nomes(q="guitar"), ["Guitar Hero III - Legends of Rock"])
        self.assertEqual(nomes(q="halo 3"), ["Halo 3"])

    def test_reindexar_mantem_id_do_jogo(self):
        antes = self.jogo_por_titulo("Halo 3")["id"]
        dados, _ = torrent_sintetico(ENTRADAS)
        store.gravar_colecao(self.con, URL, nome_colecao(URL), "/x", ler_torrent(dados))
        self.assertEqual(self.jogo_por_titulo("Halo 3")["id"], antes)


if __name__ == "__main__":
    unittest.main()
