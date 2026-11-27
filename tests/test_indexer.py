"""Testes obrigatórios da fase 1: bencode/infohash (com padding), numeração do so=, magnet, banco e varredura."""
import hashlib
import os
import sqlite3
import unittest

from app.indexer import store, update
from app.indexer.bencode import BencodeError, bdecode, bencode
from app.indexer.magnet import ler_magnet, limitar_trackers, montar_magnet
from app.indexer.torrent import acervo_de, ler_torrent, nome_colecao
from tests import referencia_original as ref
from tests.util import ENTRADAS_COM_PADS, torrent_sintetico

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
XBOX = os.path.join(os.environ.get("MINERVA_TORRENTS", os.path.join(RAIZ, "data", "torrents")),
                    "Minerva_Myrient - Redump - Microsoft - Xbox 360.torrent")
XBOX_HASH = "c98fbaa2a36ee48e3d9423be02cdbefd9281bc7d"


class TestBencode(unittest.TestCase):
    def test_ida_e_volta(self):
        v = {b"a": 1, b"b": [b"x", -5, {b"c": b""}], b"z": b"\x00\xff"}
        self.assertEqual(bdecode(bencode(v)), v)

    def test_span_do_info_e_bytes_crus(self):
        # info com chaves FORA de ordem: o hash tem que ser dos bytes do arquivo, não de uma recodificação
        info_cru = b"d4:name3:abc6:lengthi5e12:piece lengthi16384e6:pieces20:" + b"\x01" * 20 + b"e"
        dados = b"d8:announce3:x:y4:info" + info_cru + b"e"
        spans = {}
        bdecode(dados, spans)
        ini, fim = spans[b"info"]
        self.assertEqual(dados[ini:fim], info_cru)
        t = ler_torrent(dados)
        self.assertEqual(t.infohash, hashlib.sha1(info_cru).hexdigest())
        self.assertNotEqual(t.infohash, hashlib.sha1(bencode(bdecode(info_cru))).hexdigest())

    def test_info_aninhado_nao_confunde(self):
        # uma chave "info" dentro de outro dicionário não pode virar o span
        dados = bencode({"x": {"info": "falso"}, "info": {"name": "ok", "length": 1}})
        spans = {}
        bdecode(dados, spans)
        ini, fim = spans[b"info"]
        self.assertEqual(bdecode(dados[ini:fim]), {b"name": b"ok", b"length": 1})

    def test_invalido(self):
        for ruim in (b"x", b"d3:abc", b"l", b"10:curta"):
            with self.assertRaises(BencodeError):
                bdecode(ruim)


class TestTorrentEPadding(unittest.TestCase):
    def setUp(self):
        self.dados, self.hash = torrent_sintetico(ENTRADAS_COM_PADS)
        self.t = ler_torrent(self.dados)

    def test_infohash(self):
        self.assertEqual(self.t.infohash, self.hash)

    def test_pads_ocultos_mas_numeracao_preservada(self):
        self.assertEqual([(a.idx, a.nome) for a in self.t.arquivos], [
            (0, "Halo 3 (USA, Europe).zip"),
            (2, "Halo 3 (Japan).zip"),
            (4, "Guitar Hero III - Legends of Rock (USA) (Rev 1).zip"),
            (6, "Guitar Hero III - Legends of Rock (USA).zip"),
        ])
        self.assertEqual(self.t.total_entradas, 7)
        self.assertEqual(self.t.arquivos[2].pasta, "Microsoft - Xbox 360")

    def test_trackers_sem_repeticao_na_ordem_do_original(self):
        self.assertEqual(self.t.trackers, ["udp://a.example:1337/announce", "http://b.example/announce"])
        dados, _ = torrent_sintetico(ENTRADAS_COM_PADS, announce="http://só-announce/x",
                                     announce_list=(("udp://l1/a",), ("udp://l2/a", "udp://l1/a")))
        self.assertEqual(ler_torrent(dados).trackers, ["http://só-announce/x", "udp://l1/a", "udp://l2/a"])

    def test_igual_ao_original(self):
        o = ref.listar_arquivos_torrent(self.dados)
        self.assertEqual(o["infohash"], self.t.infohash)
        self.assertEqual(o["trackers"], self.t.trackers)
        self.assertEqual(o["arquivos"], [(a.idx, a.caminho, a.tamanho) for a in self.t.arquivos])

    def test_arquivo_unico_e_v2(self):
        t = ler_torrent(bencode({"info": {"name": "x.zip", "length": 9, "piece length": 16384, "pieces": b""}}))
        self.assertEqual([(a.idx, a.caminho) for a in t.arquivos], [(0, "x.zip")])
        arvore = {"b.zip": {"": {"length": 2}}, "a": {"c.zip": {"": {"length": 1}}}}
        t2 = ler_torrent(bencode({"info": {"name": "r", "file tree": arvore, "meta version": 2}}))
        self.assertEqual([(a.idx, a.caminho) for a in t2.arquivos], [(0, "a/c.zip"), (1, "b.zip")])


class TestMagnet(unittest.TestCase):
    def test_formato(self):
        m = montar_magnet(XBOX_HASH, 1884, ["udp://t.example:1337/announce"])
        self.assertEqual(m, f"magnet:?xt=urn:btih:{XBOX_HASH}&dn=Minerva_Myrient&so=1884"
                            "&tr=udp%3A%2F%2Ft.example%3A1337%2Fannounce")
        d = ler_magnet(m)
        self.assertEqual((d["infohash"], d["so"], d["dn"]), (XBOX_HASH, [1884], "Minerva_Myrient"))

    def test_igual_ao_original(self):
        trs = ["udp://a:1/x", "http://b/y?z=1&w=2"]
        self.assertEqual(montar_magnet(XBOX_HASH, 7, trs), ref.montar_magnet(XBOX_HASH, 7, trs))

    def test_limites(self):
        self.assertEqual(limitar_trackers([1, 2, 3, 4], 3), [1, 2, 3])
        self.assertEqual(limitar_trackers([1, 2], 0), [1, 2])
        with self.assertRaises(ValueError):
            montar_magnet("abc", 1)
        with self.assertRaises(ValueError):
            montar_magnet(XBOX_HASH, -1)

    def test_so_aponta_para_o_arquivo_certo_com_pads(self):
        dados, h = torrent_sintetico(ENTRADAS_COM_PADS)
        t = ler_torrent(dados)
        alvo = next(a for a in t.arquivos if "Rev 1" in a.nome)
        so = ler_magnet(montar_magnet(h, alvo.idx, t.trackers))["so"][0]
        info = bdecode(dados)[b"info"]
        self.assertEqual(info[b"files"][so][b"path"][-1].decode(), alvo.nome)


@unittest.skipUnless(os.path.exists(XBOX), "torrent real do Xbox 360 não baixado (rode: python3 -m app.indexer atualizar)")
class TestCasoRealXbox360(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(XBOX, "rb") as f:
            cls.dados = f.read()
        cls.t = ler_torrent(cls.dados)

    def test_infohash(self):
        self.assertEqual(self.t.infohash, XBOX_HASH)
        self.assertEqual(self.t.nome, "Minerva_Myrient")

    def test_so_1884(self):
        a = next(a for a in self.t.arquivos if a.idx == 1884)
        self.assertEqual(a.nome, "Guitar Hero - Aerosmith (USA, Europe).zip")
        self.assertEqual(a.pasta, "Microsoft - Xbox 360")

    def test_magnet_identico_ao_original(self):
        o = ref.listar_arquivos_torrent(self.dados)
        esperado = ref.montar_magnet(o["infohash"], 1884, o["trackers"])
        self.assertEqual(montar_magnet(self.t.infohash, 1884, self.t.trackers), esperado)
        self.assertEqual(len(o["arquivos"]), len(self.t.arquivos))


class TestBanco(unittest.TestCase):
    def setUp(self):
        self.con = store.conectar(":memory:")
        dados, self.hash = torrent_sintetico(ENTRADAS_COM_PADS)
        self.url = "https://cdn.example/Minerva_Myrient%20-%20Redump%20-%20Microsoft%20-%20Xbox%20360.torrent"
        self.cid = store.gravar_colecao(self.con, self.url, nome_colecao(self.url), "/x", ler_torrent(dados))
        self.con.commit()

    def tearDown(self):
        self.con.close()

    def nomes(self, texto, **kw):
        return [r["nome"] for r in store.buscar(self.con, texto, **kw)]

    def test_busca_trigram_e_termo_curto(self):
        self.assertEqual(self.nomes("guitar hero"), [
            "Guitar Hero III - Legends of Rock (USA) (Rev 1).zip", "Guitar Hero III - Legends of Rock (USA).zip"])
        self.assertEqual(self.nomes("halo 3"), ["Halo 3 (Japan).zip", "Halo 3 (USA, Europe).zip"])
        self.assertEqual(self.nomes("legends 1"), ["Guitar Hero III - Legends of Rock (USA) (Rev 1).zip"])
        self.assertEqual(self.nomes("halo", colecao="playstation"), [])
        self.assertEqual(self.nomes("HALO JAPAN"), ["Halo 3 (Japan).zip"])

    def test_arquivo_e_magnet(self):
        l = store.arquivo(self.con, self.cid, 4)
        m = ler_magnet(store.magnet_de(l, trackers=1))
        self.assertEqual((m["infohash"], m["so"], len(m["tr"])), (self.hash, [4], 1))
        self.assertIsNone(store.arquivo(self.con, self.cid, 1))  # pad não existe no índice

    def test_reindexar_troca_arquivos_e_fts(self):
        dados, _ = torrent_sintetico([(["Redump", "Microsoft - Xbox 360", "Fable II (USA).zip"], 5, None)])
        cid = store.gravar_colecao(self.con, self.url, nome_colecao(self.url), "/x", ler_torrent(dados))
        self.con.commit()
        self.assertEqual(cid, self.cid)
        self.assertEqual(self.nomes("halo"), [])
        self.assertEqual(self.nomes("fable"), ["Fable II (USA).zip"])
        self.con.execute("INSERT INTO busca(busca) VALUES('integrity-check')")
        store.remover_colecao(self.con, cid)
        self.assertEqual(store.stats(self.con)["arquivos"], 0)
        self.con.execute("INSERT INTO busca(busca) VALUES('integrity-check')")


class TestVarredura(unittest.TestCase):
    PAGINA = """<a href="../">..</a><a href="?C=M">sort</a>
      <a href="Minerva_Myrient%20-%20Redump%20-%20Microsoft%20-%20Xbox%20360.torrent">x</a>
      <a href="https://cdn.example/Minerva_Myrient%20-%20No-Intro%20-%20Nintendo%20-%20Game%20Boy.torrent">y</a>
      <a href="https://outro.example/a.torrent">fora</a><a href="leia.txt">txt</a>"""

    def test_links_relativos_e_absolutos(self):
        urls = update.extrair_torrents(self.PAGINA, "https://cdn.example/")
        self.assertEqual([nome_colecao(u) for u in urls],
                         ["No-Intro - Nintendo - Game Boy", "Redump - Microsoft - Xbox 360"])
        self.assertEqual(acervo_de(nome_colecao(urls[0])), "No-Intro")
        self.assertEqual(len(update.filtrar(urls, ["redump - microsoft - xbox 360"])), 1)
        self.assertEqual(len(update.filtrar(urls, None)), 2)


if __name__ == "__main__":
    unittest.main()
