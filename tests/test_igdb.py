"""IGDB com HTTP falso: token, renovação em 401, escolha do jogo e resumo."""
import json
import unittest

from app.meta.igdb import IGDB


class Falso:
    def __init__(self):
        self.tokens, self.chamadas, self.recusar_uma = 0, [], False

    def __call__(self, url, dados=None, cab=None):
        if "oauth2/token" in url:
            self.tokens += 1
            return 200, json.dumps({"access_token": f"t{self.tokens}", "expires_in": 5000000}).encode()
        self.chamadas.append((url, dados.decode(), cab["Authorization"]))
        if self.recusar_uma:
            self.recusar_uma = False
            return 401, b"{}"
        return 200, json.dumps([
            {"id": 1, "name": "Guitar Hero III: Legends of Rock - Wii Edition", "platforms": [5]},
            {"id": 2, "name": "Guitar Hero III: Legends of Rock", "summary": "Rock.", "first_release_date": 1193011200,
             "total_rating": 84.6, "genres": [{"name": "Music"}], "cover": {"image_id": "co1abc"}, "platforms": [12]},
        ]).encode()


class TestIGDB(unittest.TestCase):
    def test_busca_escolhe_nome_exato_e_resume(self):
        f = Falso()
        cli = IGDB("id", "segredo", http=f)
        g, incerto = cli.procurar("Guitar Hero III - Legends of Rock", 12)
        self.assertEqual((g["id"], incerto), (2, False))
        self.assertIn("where platforms = (12)", f.chamadas[0][1])
        self.assertIn('search "Guitar Hero III: Legends of Rock"', f.chamadas[0][1])
        d = IGDB.resumir(g)
        self.assertEqual((d["ano"], d["nota"], d["generos"], d["capa"]), (2007, 8.5, "Music", "co1abc"))

    def test_token_reaproveitado_e_renovado_em_401(self):
        f = Falso()
        cli = IGDB("id", "segredo", http=f)
        cli.procurar("A", None)
        cli.procurar("B", None)
        self.assertEqual(f.tokens, 1)
        f.recusar_uma = True
        cli.procurar("C", None)
        self.assertEqual(f.tokens, 2)
        self.assertEqual(f.chamadas[-1][2], "Bearer t2")


if __name__ == "__main__":
    unittest.main()
