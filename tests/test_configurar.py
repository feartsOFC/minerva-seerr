"""Link de configuração de uso único: 1º admin numa instalação nova, senhas, uso único e expiração."""
import os
import tempfile
import time
import unittest

try:
    from fastapi.testclient import TestClient
except ImportError:
    TestClient = None


@unittest.skipIf(TestClient is None, "fastapi não instalado")
class TestConfigurar(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        os.environ["MINERVA_SEM_LACO"] = "1"
        os.environ["MINERVA_URL"] = "https://minerva.exemplo"
        from app import config
        config.BANCO = os.path.join(self.dir.name, "t.db")
        from app import main
        self.main = main
        self.cl = TestClient(main.app)

    def tearDown(self):
        self.dir.cleanup()

    def _codigo(self, minutos=60):
        link = self.main.gerar_link_config(minutos)
        self.assertTrue(link.startswith("https://minerva.exemplo/c/"))
        return link.rsplit("/", 1)[1]

    def test_instalacao_nova_cria_o_primeiro_admin(self):
        t = self._codigo()
        self.assertIn("Primeiro administrador", self.cl.get(f"/c/{t}").text)
        r = self.cl.post("/configurar", data={"t": t, "novo_nome": "ana", "novo_senha": "senha-boa-123"})
        self.assertIn("admin ana criado", r.text)
        r = self.cl.post("/login", data={"nome": "ana", "senha": "senha-boa-123"}, follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertIn("PEDIDOS E LIMITES", self.cl.get("/ajustes").text)  # é admin

    def test_link_e_de_uso_unico_e_lista_os_usuarios(self):
        from app import config, usuarios
        from app.indexer import store
        c = store.conectar(config.BANCO)
        uid = usuarios.criar(c, "beto", "senha-velha-1")
        c.close()
        t = self._codigo()
        pagina = self.cl.get(f"/c/{t.upper()}").text  # maiúsculas também valem
        self.assertIn("beto", pagina)
        self.assertNotIn("Primeiro administrador", pagina)
        r = self.cl.post("/configurar", data={"t": t, f"senha_{uid}": "senha-nova-9"})
        self.assertIn("senha de beto definida", r.text)
        self.assertEqual(self.cl.get(f"/c/{t}").status_code, 403)
        self.assertEqual(self.cl.post("/configurar", data={"t": t}).status_code, 403)

    def test_link_expira(self):
        t = self._codigo(minutos=0)
        time.sleep(0.01)
        self.assertEqual(self.cl.get(f"/c/{t}").status_code, 403)
