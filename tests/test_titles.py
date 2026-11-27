"""Agrupamento de títulos: região, idioma, Rev, Disc, Demo/Beta/Bonus."""
import unittest

from app.catalog.titles import analisar, chave_titulo, peso_versao


class TestAnalisar(unittest.TestCase):
    def test_halo_tres_regioes_um_jogo(self):
        nomes = ["Halo 3 (USA, Europe).zip", "Halo 3 (Brazil).zip", "Halo 3 (Japan).zip"]
        a = [analisar(n) for n in nomes]
        self.assertEqual({x.chave for x in a}, {"halo 3"})
        self.assertEqual({x.titulo for x in a}, {"Halo 3"})
        self.assertEqual([x.regiao for x in a], ["USA, Europe", "Brazil", "Japan"])
        self.assertEqual(len({x.versao_chave for x in a}), 3)

    def test_rev_e_idiomas(self):
        a = analisar("Guitar Hero III - Legends of Rock (USA) (Rev 1).zip")
        self.assertEqual((a.titulo, a.regiao, a.revisao, a.tipo), ("Guitar Hero III - Legends of Rock", "USA", "Rev 1", "jogo"))
        self.assertEqual(a.versao_rotulo, "USA · Rev 1")
        b = analisar("Guitar Hero III - Legends of Rock (Europe) (En,Fr,De,Es,It).zip")
        self.assertEqual(b.idiomas, "En,Fr,De,Es,It")
        self.assertEqual(b.chave, a.chave)
        self.assertNotEqual(b.versao_chave, a.versao_chave)
        self.assertEqual(analisar("Jogo (Japan) (v1.1).zip").revisao, "v1.1")

    def test_discos_da_mesma_versao(self):
        d1 = analisar("Final Fantasy XI (USA) (Disc 1).zip")
        d2 = analisar("Final Fantasy XI (USA) (Disc 2).zip")
        self.assertEqual((d1.disco, d2.disco), ("1", "2"))
        self.assertEqual(d1.versao_chave, d2.versao_chave)
        self.assertEqual(d1.chave, d2.chave)
        p = [analisar(f"Forza Motorsport 3 (USA) ({x}).zip") for x in ("Game Disc", "Install Disc")]
        self.assertEqual((p[0].disco, p[1].disco), ("Game Disc", "Install Disc"))
        self.assertEqual(p[0].versao_chave, p[1].versao_chave)
        self.assertEqual(analisar("Jogo (Brazil) (Disco 2).zip").disco, "2")
        q = analisar("Halo 3 - ODST (USA) (Disc 2) (Multiplayer).zip")
        self.assertEqual((q.disco, q.extras), ("2 · Multiplayer", []))

    def test_demo_beta_bonus_marcados(self):
        demo = analisar("Guitar Hero - Aerosmith (USA) (Demo).zip")
        cheio = analisar("Guitar Hero - Aerosmith (USA, Europe).zip")
        self.assertEqual((demo.tipo, cheio.tipo), ("demo", "jogo"))
        self.assertEqual(demo.chave, cheio.chave)           # mesmo card, versão marcada
        self.assertIn("Demo", demo.versao_rotulo)
        self.assertEqual(analisar("Star Fox 2 (Japan) (Beta 2).zip").tipo, "beta")
        self.assertEqual(analisar("Jogo (USA) (Proto).zip").tipo, "beta")
        self.assertEqual(analisar("Halo 3 (USA) (Bonus Disc).zip").tipo, "bonus")
        self.assertEqual(analisar("Jogo (Europe) (Kiosk Demo).zip").tipo, "demo")
        self.assertEqual(analisar("Jogo (Japan) (Tentou-you Taikenban).zip").tipo, "demo")
        self.assertEqual(analisar("Jogo (USA) (Bonus Content Disc).zip").tipo, "bonus")

    def test_artigo_e_extras(self):
        a = analisar("Legend of Zelda, The - A Link to the Past (USA).zip")
        self.assertEqual(a.titulo, "The Legend of Zelda - A Link to the Past")
        b = analisar("Gran Turismo 4 (USA) (Greatest Hits).zip")
        self.assertEqual((b.titulo, b.extras), ("Gran Turismo 4", ["Greatest Hits"]))
        self.assertEqual(analisar("Jogo (USA) (Unl) [b].zip").extras, ["Unl", "b"])

    def test_chave_normaliza(self):
        self.assertEqual(chave_titulo("Pokémon: Stadium & Co."), "pokemon stadium and co")
        self.assertEqual(analisar("Sem Parenteses.zip").titulo, "Sem Parenteses")

    def test_preferencia_de_versao(self):
        vs = [analisar(n) for n in ["Halo 3 (Japan).zip", "Halo 3 (USA) (Demo).zip", "Halo 3 (USA, Europe).zip",
                                    "Halo 3 (Brazil).zip"]]
        self.assertEqual(min(vs, key=peso_versao).regiao, "Brazil")
        vs = [analisar(n) for n in ["GH3 (USA).zip", "GH3 (USA) (Rev 1).zip"]]
        self.assertEqual(min(vs, key=peso_versao).revisao, "Rev 1")


if __name__ == "__main__":
    unittest.main()
