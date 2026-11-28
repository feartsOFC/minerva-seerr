"""Pedidos com qBit falso: só o arquivo pedido é ligado, torrent compartilhado, status, limites e liberação."""
import os
import tempfile
import unittest

from app import pedidos, usuarios
from app.indexer import store
from app.indexer.torrent import ler_torrent, nome_colecao
from app.qbit import indice_por_caminho
from tests.util import torrent_sintetico

X = ["Redump", "Microsoft - Xbox 360"]
ENTRADAS = [
    (X + ["Halo 3 (USA, Europe).zip"], 100, None),
    ([".pad", "1"], 1, b"p"),                                   # pad: o qBit esconde → índices do qBit ≠ so=
    (X + ["Halo 3 (Brazil).zip"], 110, None),
    (X + ["Guitar Hero III - Legends of Rock (USA) (Rev 1).zip"], 200, None),
    (X + ["Forza Motorsport 3 (USA) (Disc 1) (Game Disc).zip"], 300, None),
    (X + ["Forza Motorsport 3 (USA) (Disc 2) (Install Disc).zip"], 50, None),
]
URL = "https://cdn.example/Minerva_Myrient%20-%20Redump%20-%20Microsoft%20-%20Xbox%20360.torrent"
GB = 1024 ** 3


class QbitFalso:
    def __init__(self, dados, infohash, livre=500 * GB):
        self.dados, self.hash, self.livre = dados, infohash, livre
        self.torrents, self.prio, self.log = {}, {}, []
        t = ler_torrent(dados)
        # o qBit esconde pads e põe a pasta-raiz no nome: índices 0..n-1 só dos arquivos reais
        self.lista = [{"index": i, "name": "Minerva_Myrient/" + a.caminho, "progress": 0.0}
                      for i, a in enumerate(t.arquivos)]

    def espaco_livre(self):
        return self.livre

    def torrent(self, h):
        return self.torrents.get(h)

    def info(self, hashes=None, tag=None):
        return [t for h, t in self.torrents.items() if not hashes or h in hashes]

    def criar_categoria(self, nome, pasta=""):
        pass

    def adicionar_torrent(self, conteudo, nome, categoria, tags, savepath=None):
        assert conteudo == self.dados
        self.log.append(("add", categoria, tags))
        self.torrents[self.hash] = {"hash": self.hash, "state": "stoppedDL", "dlspeed": 0}
        self.prio[self.hash] = {f["index"]: 1 for f in self.lista}  # entra tudo ligado, como o qBit real

    def esperar_torrent(self, h, segundos=30):
        return self.torrents[h]

    def arquivos(self, h):
        return [dict(f, priority=self.prio[h][f["index"]]) for f in self.lista]

    def prioridade(self, h, ids, prio):
        for i in ids:
            self.prio[h][i] = prio

    def iniciar(self, h):
        self.torrents[h]["state"] = "downloading"
        self.log.append(("start", h))

    def apagar(self, h, com_arquivos=True):
        self.torrents.pop(h, None)
        self.log.append(("delete", h))

    def ligados(self):
        return sorted(f["name"].rsplit("/", 1)[-1] for f in self.lista if self.prio[self.hash][f["index"]])


class TestPedidos(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.con = store.conectar(":memory:")
        self.dados, h = torrent_sintetico(ENTRADAS)
        local = os.path.join(self.tmp.name, "x.torrent")
        with open(local, "wb") as f:
            f.write(self.dados)
        store.gravar_colecao(self.con, URL, nome_colecao(URL), local, ler_torrent(self.dados))
        self.con.commit()
        self.qb = QbitFalso(self.dados, h)
        self.con.execute("INSERT INTO ajustes VALUES('ped_baixando_max', '0')")   # sem limite; ver test_um_por_vez
        self.bruno = self._usuario("bruno", True, "bruno")
        self.lucas = self._usuario("lucas", False, "lucas")

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def _usuario(self, nome, admin, pc):
        uid = usuarios.criar(self.con, nome, "senha-longa-1", admin, pc)
        return dict(self.con.execute("SELECT * FROM usuarios WHERE id=?", (uid,)).fetchone())

    def _jogo(self, titulo):
        (jid,) = self.con.execute("SELECT id FROM jogos WHERE titulo=?", (titulo,)).fetchone()
        from app.catalog import catalogo
        return catalogo.jogo(self.con, jid)

    def _pedir(self, u, titulo, rotulo):
        j = self._jogo(titulo)
        v = next(x for x in j["versoes"] if x["rotulo"] == rotulo)
        return pedidos.criar(self.con, u, j["id"], v["chave"], self.qb)

    def test_so_o_arquivo_pedido_e_ligado(self):
        p = self._pedir(self.bruno, "Guitar Hero III - Legends of Rock", "USA · Rev 1")
        self.assertEqual(p["status"], "na_fila")
        self.assertEqual(self.qb.ligados(), ["Guitar Hero III - Legends of Rock (USA) (Rev 1).zip"])
        f = p["arquivos"][0]
        self.assertEqual((f["so"], f["qbit_index"]), (3, 2))          # so conta o pad; o qBit não
        self.assertIn("&so=3", p["magnet"])
        self.assertEqual(self.qb.log[0], ("add", "minerva", "minerva"))

    def test_segundo_pedido_mesmo_torrent_so_liga_mais_um(self):
        self._pedir(self.bruno, "Halo 3", "Brazil")
        self._pedir(self.lucas, "Forza Motorsport 3", "USA")          # 2 discos
        self.assertEqual(sum(1 for x in self.qb.log if x[0] == "add"), 1)
        self.assertEqual(self.qb.ligados(), ["Forza Motorsport 3 (USA) (Disc 1) (Game Disc).zip",
                                             "Forza Motorsport 3 (USA) (Disc 2) (Install Disc).zip",
                                             "Halo 3 (Brazil).zip"])

    def test_status_progresso_e_entrega(self):
        p = self._pedir(self.lucas, "Forza Motorsport 3", "USA")
        h = self.qb.hash
        self.qb.lista[3]["progress"] = 1.0                               # disco 1 (300) pronto, disco 2 (50) em 0
        pedidos.sincronizar(self.con, self.qb)
        q = pedidos.pedido(self.con, p["id"])
        self.assertEqual(q["status"], "baixando")
        self.assertAlmostEqual(q["progresso"], 300 / 350, places=3)
        self.qb.lista[4]["progress"] = 1.0
        pedidos.sincronizar(self.con, self.qb)
        self.assertEqual(pedidos.pedido(self.con, p["id"])["status"], "baixado")
        pend = pedidos.entregas_pendentes(self.con)
        self.assertEqual([(e["id"], e["pc"], e["destino_relativo"]) for e in pend],
                         [(p["id"], "lucas", "Jogos/Microsoft - Xbox 360")])
        self.assertEqual(pend[0]["arquivos"][0]["qbit_nome"],
                         "Minerva_Myrient/Redump/Microsoft - Xbox 360/Forza Motorsport 3 (USA) (Disc 1) (Game Disc).zip")
        r = pedidos.relatar_entrega(self.con, p["id"], "entregue", None, "D:/torrents/Jogos/Microsoft - Xbox 360", self.qb)
        self.assertEqual(r["torrents_apagados"], [h])                    # ninguém mais usava: torrent sai do qBit
        self.assertIsNone(self.qb.torrent(h))
        self.assertEqual(pedidos.pedido(self.con, p["id"])["status"], "entregue")

    def test_liberar_com_torrent_compartilhado(self):
        a = self._pedir(self.bruno, "Halo 3", "Brazil")
        self._pedir(self.lucas, "Halo 3", "Brazil")                    # mesmo arquivo, outro dono
        b = self._pedir(self.lucas, "Guitar Hero III - Legends of Rock", "USA · Rev 1")
        self.con.execute("UPDATE pedidos SET status='baixado' WHERE id IN (?,?)", (a["id"], b["id"]))
        r = pedidos.relatar_entrega(self.con, b["id"], "entregue", None, "x", self.qb)
        self.assertEqual(r["apagar_arquivos"], ["Minerva_Myrient/Redump/Microsoft - Xbox 360/Guitar Hero III - Legends of Rock (USA) (Rev 1).zip"])
        self.assertIsNotNone(self.qb.torrent(self.qb.hash))
        r = pedidos.relatar_entrega(self.con, a["id"], "entregue", None, "x", self.qb)
        self.assertEqual(r, {"apagar_arquivos": [], "torrents_apagados": []})   # o do lucas ainda usa o Halo
        self.assertIn("Halo 3 (Brazil).zip", self.qb.ligados())

    def test_limites(self):
        self.con.execute("INSERT INTO ajustes VALUES('ped_limite_gb', '0')")
        with self.assertRaisesRegex(pedidos.ErroPedido, "limite por pedido"):
            self._pedir(self.bruno, "Halo 3", "Brazil")
        self.con.execute("UPDATE ajustes SET valor='20' WHERE chave='ped_limite_gb'")
        self.con.execute("INSERT INTO ajustes VALUES('ped_pedidos_por_dia', '1')")
        self._pedir(self.lucas, "Halo 3", "Brazil")
        with self.assertRaisesRegex(pedidos.ErroPedido, "24 h"):
            self._pedir(self.lucas, "Halo 3", "USA, Europe")
        self._pedir(self.bruno, "Halo 3", "USA, Europe")                 # admin não tem limite diário
        with self.assertRaisesRegex(pedidos.ErroPedido, "já pediu"):
            self._pedir(self.bruno, "Halo 3", "USA, Europe")

    def test_sem_espaco_espera_e_volta(self):
        self.qb.livre = 10 * GB
        p = self._pedir(self.bruno, "Halo 3", "Brazil")
        self.assertEqual(p["status"], "sem_espaco")
        self.assertEqual(self.qb.torrents, {})
        self.qb.livre = 500 * GB
        pedidos.sincronizar(self.con, self.qb)                           # reenvia e já lê o estado no qBit
        self.assertEqual(pedidos.pedido(self.con, p["id"])["status"], "baixando")
        self.assertEqual(self.qb.ligados(), ["Halo 3 (Brazil).zip"])

    def test_aprovacao_e_cancelar(self):
        self.con.execute("INSERT INTO ajustes VALUES('ped_aprovacao', 'true')")
        p = self._pedir(self.lucas, "Halo 3", "Brazil")
        self.assertEqual(p["status"], "aguardando_aprovacao")
        self.assertEqual(self.qb.torrents, {})
        pedidos.aprovar(self.con, p["id"], self.bruno, self.qb)
        self.assertEqual(pedidos.pedido(self.con, p["id"])["status"], "na_fila")
        pedidos.cancelar(self.con, p["id"], self.qb)
        self.assertEqual(pedidos.pedido(self.con, p["id"])["status"], "cancelado")
        self.assertIsNone(self.qb.torrent(self.qb.hash))

    def test_um_por_vez(self):
        self.con.execute("UPDATE ajustes SET valor='1' WHERE chave='ped_baixando_max'")
        a = self._pedir(self.bruno, "Halo 3", "Brazil")
        b = self._pedir(self.lucas, "Guitar Hero III - Legends of Rock", "USA · Rev 1")
        c = self._pedir(self.lucas, "Forza Motorsport 3", "USA")
        self.assertEqual(a["status"], "na_fila")
        self.assertEqual((b["status"], c["status"]), ("aguardando_vez", "aguardando_vez"))
        self.assertEqual(b["erro"], pedidos.MSG_VEZ)
        self.assertEqual(self.qb.ligados(), ["Halo 3 (Brazil).zip"])           # só o 1º foi para o qBit
        pedidos.sincronizar(self.con, self.qb)                                   # a ainda baixando: ninguém anda
        self.assertEqual(pedidos.pedido(self.con, b["id"])["status"], "aguardando_vez")
        self.qb.lista[1]["progress"] = 1.0                                       # Halo 3 (Brazil) terminou
        pedidos.sincronizar(self.con, self.qb)
        self.assertEqual(pedidos.pedido(self.con, a["id"])["status"], "baixado")  # baixado libera a vaga
        q = pedidos.pedido(self.con, b["id"])
        self.assertEqual((q["status"], q["erro"]), ("baixando", None))           # o próximo por ordem de pedido
        self.assertEqual(pedidos.pedido(self.con, c["id"])["status"], "aguardando_vez")
        pedidos.cancelar(self.con, b["id"], self.qb)                             # cancelar também libera
        pedidos.sincronizar(self.con, self.qb)
        self.assertEqual(pedidos.pedido(self.con, c["id"])["status"], "baixando")
        pedidos.cancelar(self.con, c["id"], self.qb)

    def test_sem_espaco_respeita_a_vez(self):
        self.con.execute("UPDATE ajustes SET valor='1' WHERE chave='ped_baixando_max'")
        self.qb.livre = 10 * GB
        a = self._pedir(self.bruno, "Halo 3", "Brazil")
        self.assertEqual(a["status"], "sem_espaco")                              # sem espaço não ocupa vaga
        self.qb.livre = 500 * GB
        b = self._pedir(self.lucas, "Guitar Hero III - Legends of Rock", "USA · Rev 1")
        self.assertEqual(b["status"], "na_fila")
        pedidos.sincronizar(self.con, self.qb)
        self.assertEqual(pedidos.pedido(self.con, a["id"])["status"], "aguardando_vez")

    def test_indice_por_caminho(self):
        lista = [{"index": 0, "name": "Minerva_Myrient/A/b.zip"}, {"index": 1, "name": "Minerva_Myrient\\A\\c.zip"}]
        self.assertEqual(indice_por_caminho(lista, "A/c.zip"), 1)
        self.assertIsNone(indice_por_caminho(lista, "b.zip/x"))


class TestUsuarios(unittest.TestCase):
    def test_senha_e_sessao(self):
        con = store.conectar(":memory:")
        usuarios.criar(con, "bruno", "uma-senha-boa", True, "bruno")
        self.assertIsNone(usuarios.autenticar(con, "bruno", "errada"))
        u = usuarios.autenticar(con, "BRUNO", "uma-senha-boa")
        self.assertEqual(u["papel"], "admin")
        tok = usuarios.abrir_sessao(con, u["id"])
        self.assertEqual(usuarios.da_sessao(con, tok)["nome"], "bruno")
        usuarios.trocar_senha(con, "bruno", "outra-senha-boa")
        self.assertIsNone(usuarios.da_sessao(con, tok))                  # trocar senha derruba as sessões
        with self.assertRaises(ValueError):
            usuarios.criar(con, "x", "curta")
        con.close()


if __name__ == "__main__":
    unittest.main()
