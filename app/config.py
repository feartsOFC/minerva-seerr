"""Configuração por variáveis de ambiente (.env no Compose). Ajustes editáveis na tela ficam no banco."""
import os

DADOS = os.environ.get("MINERVA_DADOS", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data"))
BANCO = os.environ.get("MINERVA_BANCO", os.path.join(DADOS, "minerva.db"))
PASTA_TORRENTS = os.environ.get("MINERVA_TORRENTS", os.path.join(DADOS, "torrents"))
PASTA_CAPAS = os.environ.get("MINERVA_CAPAS", os.path.join(DADOS, "capas"))
INDICE_URL = os.environ.get("MINERVA_INDICE_URL", "https://cdn.minerva-archive.org/")

# MVP: só estas coleções. "*" libera o catálogo inteiro.
MVP = [
    "Redump - Microsoft - Xbox 360",
    "Redump - Sony - PlayStation 2",
    "No-Intro - Nintendo - Super Nintendo Entertainment System",
    "No-Intro - Nintendo - Nintendo 64 (BigEndian)",
    # complementos do Xbox 360: DLC/Addon e title updates (não viram jogos; ficam presos ao jogo-base)
    "No-Intro - Microsoft - Xbox 360 (Digital)",
    "No-Intro - Unofficial - Microsoft - Xbox 360 (Title Updates)",
]
_col = os.environ.get("MINERVA_COLECOES", "").strip()
COLECOES = None if _col == "*" else ([c.strip() for c in _col.split(";") if c.strip()] or MVP)

TRACKERS_NO_MAGNET = int(os.environ.get("MINERVA_TRACKERS", "10"))  # 0 = todos
