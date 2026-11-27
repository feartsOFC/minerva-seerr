"""DLC e title updates: ligação ao jogo-base, página do jogo, pedido com complementos, limite e subpastas."""
import os
import tempfile
import unittest

from app import pedidos, usuarios
from app.catalog import catalogo
from app.indexer import store
from app.indexer.torrent import ler_torrent, nome_colecao
from tests.util import torrent_sintetico

MB = 1024 ** 2
R = ["Redump", "Microsoft - Xbox 360"]
D = ["No-Intro", "Microsoft - Xbox 360 (Digital)"]
T = ["No-Intro", "Unofficial - Microsoft - Xbox 360 (Title Updates)"]
BASE = [
    (R + ["Guitar Hero III - Legends of Rock (USA) (En,Fr,De,Es,It) (Rev 1).zip"], 7000 * MB, None),
    (R + ["Halo 3 (USA, Europe).zip"], 6000 * MB, None),
    (R + ["Fallout 3 (USA).zip"], 5000 * MB, None),
    (R + ["Assassin's Creed II (USA).zip"], 6500 * MB, None),
    (R + ["Minecraft - Xbox 360 Edition (World).zip"], 900 * MB, None),
    (R + ["Castlevania - Lords of Shadow (USA) (Disc 1).zip"], 7000 * MB, None),
    (R + ["Castlevania - Harmony of Despair (USA).zip"], 500 * MB, None),
]
DIGITAL = [
    (D + ["Guitar Hero III - Legends of Rock - Muse Track Pack (World) (DLC).zip"], 55 * MB, None),
    (D + ["Guitar Hero III - Legends of Rock - Metallica 'Death Magnetic' Album (World) (DLC).zip"], 1313 * MB, None),
    (D + ["Halo 3 - Mythic Map Pack (World) (DLC).zip"], 293 * MB, None),
    (D + ["Halo 3 - Heroic Map Pack (World) (Addon).zip"], 440 * MB, None),
    (D + ["Fallout 3 (World) (425307D5) (Addon).zip"], 1 * MB, None),
    (D + ["Fallout 3 - Broken Steel (World) (DLC).zip"], 535 * MB, None),
    (D + ["Banjo-Kazooie (World) (XBLA).zip"], 150 * MB, None),
    (D + ["Minecraft - Skin Pack 1 (World) (DLC).zip"], 5 * MB, None),               # base só pelo começo
    (D + ["Castlevania - Reverie (World) (DLC).zip"], 300 * MB, None),               # 2 Castlevania: ambíguo, fora             # jogo XBLA: fica fora
    (D + ["Jogo Que Nao Existe - Mapa (World) (DLC).zip"], 10 * MB, None),  # DLC sem jogo-base: fica fora
]
UPDATES = [
    (T + ["Halo 3 (World) (v2).zip"], 1 * MB, None),
    (T + ["Halo 3 (World) (v3).zip"], 1 * MB, None),
    (T + ["Halo 3 (World) (Fr) (v3).zip"], 1 * MB, None),
    (T + ["Halo 3 (World) (v2) (Exclusive Demo Public Delta).zip"], 1, None),  # demo: fora
    (T + ["Assassin's Creed 2 (World) (v1).zip"], 2 * MB, None),                # 2 ↔ II
]


def _url(partes):
    return "https://cdn.example/" + ("Minerva_Myrient - " + " - ".join([partes[0], partes[1]]) + ".torrent").replace(" ", "%20")


class QbitVarios:
    """qBit falso com vários torrents (um por coleção)."""

    def __init__(self, conteudos):
        self.conteudos = conteudos  # hash → bytes
        self.torrents, self.prio, self.listas = {}, {}, {}

    def espaco_livre(self):
        return 10 ** 13

    def torrent(self, h):
        return self.torrents.get(h)

    def info(self, hashes=None, tag=None):
        return [t for h, t in self.torrents.items() if not hashes or h in hashes]

    def criar_categoria(self, *a):
        pass

    def adicionar_torrent(self, conteudo, nome, categoria, tags, savepath=None):
        h = next(h for h, c in self.conteudos.items() if c == conteudo)
        self.listas[h] = [{"index": i, "name": "Minerva_Myrient/" + a.caminho, "progress": 0.0}
                          for i, a in enumerate(ler_torrent(conteudo).arquivos)]
        self.torrents[h] = {"hash": h, "state": "stoppedDL", "dlspeed": 0}
        self.prio[h] = {f["index"]: 1 for f in self.listas[h]}

    def esperar_torrent(self, h, segundos=30):
        return self.torrents[h]

    def arquivos(self, h):
        return [dict(f, priority=self.prio[h][f["index"]]) for f in self.listas[h]]

    def prioridade(self, h, ids, prio):
        for i in ids:
            self.prio[h][i] = prio

    def iniciar(self, h):
        self.torrents[h]["state"] = "downloading"

    def apagar(self, h, com_arquivos=True):
        self.torrents.pop(h, None)

    def ligados(self):
        return sorted(f["name"].rsplit("/", 1)[-1] for h in self.listas for f in self.listas[h] if self.prio[h][f["index"]])


class TestDLC(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.con = store.conectar(":memory:")
        conteudos = {}
        # coleção de DLC ANTES da base: a ligação tem que acontecer quando a base chega
        for entradas in (DIGITAL, UPDATES, BASE):
            dados, h = torrent_sintetico(entradas)
            url = _url(entradas[0][0])
            local = os.path.join(self.tmp.name, h + ".torrent")
            with open(local, "wb") as fh:
                fh.write(dados)
            store.gravar_colecao(self.con, url, nome_colecao(url), local, ler_torrent(dados))
            conteudos[h] = dados
        self.con.commit()
        self.qb = QbitVarios(conteudos)
        uid = usuarios.criar(self.con, "lucas", "senha-longa-1", False, "lucas")
        self.u = dict(self.con.execute("SELECT * FROM usuarios WHERE id=?", (uid,)).fetchone())

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def jogo(self, titulo):
        (jid,) = self.con.execute("SELECT id FROM jogos WHERE titulo=?", (titulo,)).fetchone()
        return catalogo.jogo(self.con, jid)

    def test_dlc_presa_ao_jogo_e_fora_das_versoes(self):
        gh = self.jogo("Guitar Hero III - Legends of Rock")
        self.assertEqual(len(gh["versoes"]), 1)
        self.assertEqual([d["rotulo"] for d in gh["dlcs"]], ["Metallica 'Death Magnetic' Album", "Muse Track Pack"])
        halo = self.jogo("Halo 3")
        self.assertEqual(sorted(d["rotulo"] for d in halo["dlcs"]), ["Heroic Map Pack", "Mythic Map Pack"])
        fo = self.jogo("Fallout 3")
        self.assertEqual(sorted(d["rotulo"] for d in fo["dlcs"]), ["Broken Steel", "Conteúdo extra (425307D5)"])
        # nada de jogo novo vindo das coleções de complemento
        titulos = {r[0] for r in self.con.execute("SELECT titulo FROM jogos WHERE qtd_versoes>0")}
        self.assertEqual(len(titulos), 7)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM arquivos WHERE tipo='avulso'").fetchone()[0], 4)

    def test_casamento_por_romano_e_por_comeco_unico(self):
        self.assertEqual([d["rotulo"] for d in self.jogo("Minecraft - Xbox 360 Edition")["dlcs"]], ["Skin Pack 1"])
        self.assertEqual([u["rotulo"] for u in self.jogo("Assassin's Creed II")["atualizacoes"]], ["v1 · World"])
        self.assertEqual(self.jogo("Castlevania - Lords of Shadow")["dlcs"], [])

    def test_atualizacoes_com_a_mais_recente_marcada(self):
        ups = self.jogo("Halo 3")["atualizacoes"]
        self.assertEqual([(u["rotulo"], u["recente"]) for u in ups],
                         [("v3 · World", True), ("v2 · World", False), ("v3 · World · Fr", True)])

    def test_tamanho_da_versao_nao_soma_dlc(self):
        (tam,) = self.con.execute("SELECT tamanho_princ FROM jogos WHERE titulo='Halo 3'").fetchone()
        self.assertEqual(tam, 6000 * MB)

    def test_pedido_com_dlc_liga_os_arquivos_e_usa_subpastas(self):
        gh = self.jogo("Guitar Hero III - Legends of Rock")
        muse = next(d for d in gh["dlcs"] if d["rotulo"] == "Muse Track Pack")
        p = pedidos.criar(self.con, self.u, gh["id"], gh["principal"], self.qb, extras=[muse["id"]])
        self.assertEqual(p["tamanho"], 7055 * MB)
        self.assertEqual(p["versao_rotulo"], gh["versoes"][0]["rotulo"] + " + 1 DLC")
        self.assertEqual(self.qb.ligados(), ["Guitar Hero III - Legends of Rock (USA) (En,Fr,De,Es,It) (Rev 1).zip",
                                             "Guitar Hero III - Legends of Rock - Muse Track Pack (World) (DLC).zip"])
        self.assertEqual(sorted(f["subpasta"] for f in p["arquivos"]), ["", "DLC/Guitar Hero III - Legends of Rock"])
        self.assertEqual(len({f["infohash"] for f in p["arquivos"]}), 2)

    def test_so_dlc_para_quem_ja_tem_o_jogo(self):
        halo = self.jogo("Halo 3")
        ids = [d["id"] for d in halo["dlcs"]] + [halo["atualizacoes"][1]["id"]]
        p = pedidos.criar(self.con, self.u, halo["id"], "", self.qb, extras=ids)
        self.assertEqual(p["versao_rotulo"], "Só 2 DLC + 1 atualização")
        self.assertEqual(p["tamanho"], (293 + 440 + 1) * MB)
        self.assertIn("Updates/Halo 3", {f["subpasta"] for f in p["arquivos"]})
        with self.assertRaises(pedidos.ErroPedido):  # mesma DLC de novo
            pedidos.criar(self.con, self.u, halo["id"], "", self.qb, extras=[ids[0]])

    def test_dlc_conta_no_limite(self):
        store.definir_ajuste(self.con, "ped_limite_gb", 7)
        gh = self.jogo("Guitar Hero III - Legends of Rock")
        metallica = next(d for d in gh["dlcs"] if "Metallica" in d["rotulo"])
        with self.assertRaisesRegex(pedidos.ErroPedido, "limite"):
            pedidos.criar(self.con, self.u, gh["id"], gh["principal"], self.qb, extras=[metallica["id"]])
        pedidos.criar(self.con, self.u, gh["id"], "", self.qb, extras=[metallica["id"]])  # sozinha cabe

    def test_dlc_de_outro_jogo_e_recusada(self):
        gh, halo = self.jogo("Guitar Hero III - Legends of Rock"), self.jogo("Halo 3")
        with self.assertRaises(pedidos.ErroPedido):
            pedidos.criar(self.con, self.u, gh["id"], "", self.qb, extras=[halo["dlcs"][0]["id"]])
        with self.assertRaises(pedidos.ErroPedido):
            pedidos.criar(self.con, self.u, gh["id"], "", self.qb, extras=[])

    def test_worker_recebe_subpasta(self):
        store.definir_ajuste(self.con, "ped_aprovacao", False)
        halo = self.jogo("Halo 3")
        p = pedidos.criar(self.con, self.u, halo["id"], halo["principal"], self.qb, extras=[halo["dlcs"][0]["id"]])
        self.con.execute("UPDATE pedidos SET status='baixado' WHERE id=?", (p["id"],))
        e = pedidos.entregas_pendentes(self.con)[0]
        self.assertEqual(sorted(a["subpasta"] for a in e["arquivos"]), ["", "DLC/Halo 3"])

    def test_pasta_windows(self):
        self.assertEqual(pedidos.pasta_windows("Assassin's Creed: Brotherhood"), "Assassins Creed Brotherhood")
        self.assertEqual(pedidos.pasta_windows("Pokémon Stadium 2"), "Pokemon Stadium 2")
