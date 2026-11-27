"""Casamento de capas do libretro (sem rede: lista injetada)."""
import unittest

from app.meta import libretro
from app.meta.capas import placeholder

LISTA = ["Super Mario World (USA)", "Super Mario World (Europe) (Rev 1)", "Legend of Zelda, The - A Link to the Past (USA)",
         "Tales of Destiny 2 (Japan, Asia)", "World Soccer Winning Eleven 8 - Liveware Evolution (Japan)",
         "Rap Jam - Volume One (USA) (En,Fr,Es)", "Mario & Wario (Japan)", "Final Fantasy X (USA)"]


class TestLibretro(unittest.TestCase):
    def setUp(self):
        self.ind = libretro.Indice(LISTA)

    def test_exato_pela_versao_preferida(self):
        self.assertEqual(self.ind.casar(["Super Mario World (Europe) (Rev 1).zip", "Super Mario World (USA).zip"]),
                         ("Super Mario World (USA)", False))

    def test_mesmo_titulo_outra_regiao_e_artigo(self):
        self.assertEqual(self.ind.casar(["Super Mario World (Japan).zip"]), ("Super Mario World (USA)", False))
        self.assertEqual(self.ind.casar(["Legend of Zelda, The - A Link to the Past (Europe).zip"])[0],
                         "Legend of Zelda, The - A Link to the Past (USA)")

    def test_sanitiza_caracteres(self):
        ind = libretro.Indice(["Mario _ Wario (Japan)"])
        self.assertEqual(ind.casar(["Mario & Wario (Japan).zip"]), ("Mario _ Wario (Japan)", False))

    def test_aproximado_incerto_mas_nunca_outra_continuacao(self):
        self.assertEqual(self.ind.casar(["RapJam - Volume One (USA).zip"]), ("Rap Jam - Volume One (USA) (En,Fr,Es)", True))
        self.assertIsNone(self.ind.casar(["Tales of Destiny (USA).zip"]))
        self.assertIsNone(self.ind.casar(["World Soccer Winning Eleven 10 - Liveware Evolution (Japan).zip"]))
        self.assertEqual(libretro.numeros("final fantasy x"), {"10"})
        self.assertEqual(libretro.numeros("super robot taisen scramble commander the 2nd"), {"2"})

    def test_url_e_placeholder(self):
        self.assertTrue(libretro.url_capa("Sony_-_PlayStation_2", "A & B (USA)").endswith("/Named_Boxarts/A%20%26%20B%20%28USA%29.png"))
        svg = placeholder("Guitar Hero III - Legends of <Rock>", "X360")
        self.assertIn("&lt;ROCK&gt;", svg)
        self.assertNotIn("#ffd23f", svg)  # selo amarelo vem do HTML; no SVG duplicava
        self.assertNotIn(">X360<", svg)


if __name__ == "__main__":
    unittest.main()
