"""Interface web: login, páginas e ajustes (precisa do FastAPI — roda no container)."""
import os
import tempfile
import unittest

try:
    from fastapi.testclient import TestClient
except ImportError:  # host sem fastapi
    TestClient = None


@unittest.skipIf(TestClient is None, "fastapi não instalado")
class TestWeb(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        os.environ["MINERVA_SEM_LACO"] = "1"
        from app import config
        config.BANCO = os.path.join(cls.dir.name, "t.db")
        from app import main, usuarios
        from app.indexer import store
        c = store.conectar(config.BANCO)
        usuarios.criar(c, "bruno", "senha-boa-1", admin=True, pc="bruno")
        usuarios.criar(c, "lucas", "senha-boa-2", pc="lucas")
        c.close()
        cls.app = main.app

    def cliente(self, nome=None, senha=None):
        cl = TestClient(self.app)
        if nome:
            r = cl.post("/login", data={"nome": nome, "senha": senha}, follow_redirects=False)
            self.assertEqual(r.status_code, 303)
        return cl

    def test_sem_login_vai_para_login(self):
        r = self.cliente().get("/", follow_redirects=False)
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/login"))
        self.assertIn("MINERVA", self.cliente().get("/login").text)

    def test_senha_errada(self):
        r = self.cliente().post("/login", data={"nome": "bruno", "senha": "x"})
        self.assertEqual(r.status_code, 401)

    def test_paginas(self):
        cl = self.cliente("bruno", "senha-boa-1")
        for url in ("/", "/solicitacoes", "/ajustes", "/parcial/catalogo?q=zzz", "/parcial/solicitacoes"):
            self.assertEqual(cl.get(url).status_code, 200, url)
        self.assertIn("PEDIDOS E LIMITES", cl.get("/ajustes").text)
        self.assertEqual(cl.get("/static/minerva.css").status_code, 200)

    def test_usuario_comum_nao_ve_nem_muda_ajustes(self):
        cl = self.cliente("lucas", "senha-boa-2")
        self.assertNotIn("PEDIDOS E LIMITES", cl.get("/ajustes").text)
        self.assertIn("só o admin", cl.post("/ajustes/pedidos", data={"limite_gb": "999"}).text)
        self.assertEqual(cl.post("/api/indexador/atualizar").status_code, 403)
        self.assertEqual(self.cliente().post("/api/capas/casar").status_code, 401)

    def test_admin_salva_limites(self):
        cl = self.cliente("bruno", "senha-boa-1")
        r = cl.post("/ajustes/pedidos", data={"limite_gb": "25", "pedidos_por_dia": "4", "espaco_minimo_gb": "40", "baixando_max": "2",
                                              "destino_pc": "Jogos\\{plataforma}", "qbit_url": "http://qbit.exemplo:8080"})
        self.assertIn("✓", r.text)
        from app import config, pedidos
        from app.indexer import store
        c = store.conectar(config.BANCO)
        a = pedidos.ajustes(c)
        c.close()
        self.assertEqual((a["limite_gb"], a["destino_pc"], a["aprovacao"], a["baixando_max"]),
                         (25, "Jogos/{plataforma}", False, 2))
        self.assertIn("✗", cl.post("/ajustes/pedidos", data={"limite_gb": "0", "pedidos_por_dia": "4",
                                                               "espaco_minimo_gb": "40", "destino_pc": "x"}).text)

    def test_trocar_senha(self):
        from app import config, usuarios
        from app.indexer import store
        c = store.conectar(config.BANCO)
        usuarios.criar(c, "teste", "senha-velha-1")
        c.close()
        cl = self.cliente("teste", "senha-velha-1")
        self.assertIn("não batem", cl.post("/ajustes/senha", data={"atual": "senha-velha-1", "nova": "a" * 9, "nova2": "b" * 9}).text)
        self.assertIn("errada", cl.post("/ajustes/senha", data={"atual": "x", "nova": "a" * 9, "nova2": "a" * 9}).text)
        self.assertIn("✓", cl.post("/ajustes/senha", data={"atual": "senha-velha-1", "nova": "senha-nova-9", "nova2": "senha-nova-9"}).text)
        self.assertEqual(cl.get("/ajustes", follow_redirects=False).status_code, 200)  # continua logado
        self.cliente("teste", "senha-nova-9")
