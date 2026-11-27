"""Agrupamento de nomes No-Intro/Redump em jogos e versões.

"Halo 3 (USA, Europe).zip", "Halo 3 (Brazil).zip" e "Halo 3 (Japan).zip" → jogo "Halo 3", três versões.
Os discos ("Disc 1", "Disc 2") da mesma versão ficam juntos: pedir a versão pede todos os discos.
"""
import re
import unicodedata
from dataclasses import dataclass, field

REGIOES = {
    "USA", "Europe", "Japan", "World", "Brazil", "Korea", "Asia", "Australia", "France", "Germany", "Spain",
    "Italy", "Canada", "China", "Taiwan", "Hong Kong", "Netherlands", "Sweden", "Russia", "Scandinavia", "UK",
    "United Kingdom", "Latin America", "Mexico", "Argentina", "India", "Poland", "Portugal", "Greece", "Denmark",
    "Finland", "Norway", "Belgium", "Austria", "Switzerland", "New Zealand", "South Africa", "Ireland", "Croatia",
    "Turkey", "Israel", "Unknown", "Czech", "Hungary", "Singapore", "Thailand", "Indonesia", "Philippines",
    "Malaysia", "Ukraine", "Slovakia", "Slovenia", "Serbia", "Romania", "Bulgaria", "Iceland", "Estonia", "Latvia",
    "Lithuania", "United Arab Emirates", "Saudi Arabia", "Chile", "Colombia", "Peru", "Venezuela", "Macau",
}
IDIOMA_RE = re.compile(r"^[A-Z][a-z](-[A-Za-z]{2,4})?$")
REV_RE = re.compile(r"^(Rev [\w.\-]+|v\d[\w.\-]*|Version [\w.\-]+)$", re.IGNORECASE)
DISCO_RE = re.compile(r"^(?:Dis[ck]o?|Disque) (\d+|[A-Z])(?: of \d+)?$", re.IGNORECASE)
# Partes nomeadas de um jogo de vários discos (Xbox 360): vão juntas na mesma versão
PARTE_RE = re.compile(r"^((Content )?Install(ation)?|Game|Play|Data|Video|Campaign|Multiplayer)( Dis[ck])?$", re.I)
TIPOS = [  # (regex do flag, tipo) — a ordem decide quando há mais de um
    (re.compile(r"\bBonus\b|^Add-On Content Dis[ck]", re.I), "bonus"),
    (re.compile(r"\bDemo\b|^Kiosk$|^Trial( Edition)?$|^Sample$|^Preview$|Taikenban", re.I), "demo"),
    (re.compile(r"^(Beta|Proto|Prototype|Alpha|Pre-Production)( \d+)?$", re.I), "beta"),
]
ROTULO_TIPO = {"jogo": "", "demo": "Demo", "beta": "Beta", "bonus": "Bonus Disc"}
EXTENSOES = re.compile(r"\.(zip|7z|rar)$", re.I)


@dataclass
class NomeAnalisado:
    titulo: str                  # título para exibição ("The Legend of Zelda - A Link to the Past")
    chave: str                   # chave de agrupamento normalizada
    regiao: str = ""             # "USA, Europe"
    idiomas: str = ""            # "En,Fr,De"
    revisao: str = ""            # "Rev 1", "v1.1"
    disco: str = ""              # "1", "2", "A"
    tipo: str = "jogo"           # jogo | demo | beta | bonus
    extras: list = field(default_factory=list)   # flags restantes ("Platinum", "Unl"…)

    @property
    def versao_chave(self) -> str:
        """Identifica a versão (sem o disco): é o que o usuário escolhe na página do jogo."""
        return "|".join([self.regiao, self.idiomas, self.revisao, self.tipo, ";".join(self.extras)])

    @property
    def versao_rotulo(self) -> str:
        partes = [self.regiao or "Sem região"]
        if self.idiomas:
            partes[0] += f" ({self.idiomas})"
        if self.revisao:
            partes.append(self.revisao)
        if self.tipo != "jogo":
            partes.append(ROTULO_TIPO[self.tipo])
        partes += self.extras
        return " · ".join(partes)


def _artigo_na_frente(titulo: str) -> str:
    """No-Intro escreve "Legend of Zelda, The - A Link to the Past"; vira "The Legend of Zelda - …"."""
    def trocar(seg: str) -> str:
        m = re.match(r"^(.*), (The|A|An|Les|Le|La|Los|Las|El|Il|Der|Die|Das)$", seg)
        return f"{m.group(2)} {m.group(1)}" if m else seg
    return " - ".join(trocar(s) for s in titulo.split(" - "))


def chave_titulo(titulo: str) -> str:
    t = unicodedata.normalize("NFKD", titulo).encode("ascii", "ignore").decode()
    t = t.casefold().replace("&", " and ")
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def analisar(nome_arquivo: str) -> NomeAnalisado:
    base = EXTENSOES.sub("", nome_arquivo.rsplit("/", 1)[-1]).strip()
    # tudo antes do primeiro " (" ou " [" é o título; o resto são flags
    m = re.search(r"\s[\(\[]", base)
    titulo_cru = base[:m.start()].strip() if m else base
    flags = re.findall(r"\(([^()]*)\)|\[([^\[\]]*)\]", base[m.start():] if m else "")
    flags = [a or b for a, b in flags]

    titulo = _artigo_na_frente(titulo_cru) or base
    r = NomeAnalisado(titulo=titulo, chave=chave_titulo(titulo))
    for f in flags:
        f = f.strip()
        if not f:
            continue
        itens = [x.strip() for x in f.split(",")]
        if not r.regiao and all(x in REGIOES for x in itens):
            r.regiao = ", ".join(itens)
        elif not r.idiomas and all(IDIOMA_RE.match(x) for x in itens):
            r.idiomas = ",".join(itens)
        elif not r.revisao and REV_RE.match(f):
            r.revisao = f
        elif not r.disco and DISCO_RE.match(f):
            r.disco = DISCO_RE.match(f).group(1)
        elif PARTE_RE.match(f):
            r.disco = f"{r.disco} · {f}" if r.disco else f
        else:
            for rx, tipo in TIPOS:
                if rx.search(f):
                    if r.tipo == "jogo":
                        r.tipo = tipo
                    break
            else:
                r.extras.append(f)
    return r


# Ordem de preferência para escolher a versão "principal" (capa, tamanho no card, padrão do seletor)
PREFERENCIA_REGIAO = ["Brazil", "USA", "World", "Europe", "Japan"]


def peso_versao(a: NomeAnalisado) -> tuple:
    regs = [x.strip() for x in a.regiao.split(",")]
    pos = min((PREFERENCIA_REGIAO.index(x) for x in regs if x in PREFERENCIA_REGIAO), default=len(PREFERENCIA_REGIAO))
    rev = re.findall(r"\d+", a.revisao)
    return (a.tipo != "jogo", bool(a.extras), pos, -(int(rev[0]) if rev else 0))
