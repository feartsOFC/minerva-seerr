"""Usuários (admin ou usuário comum), cada um ligado ao PC que recebe os jogos (opcional).

CLI:  python -m app.usuarios criar ana --admin --pc ana --gerar   (mostra a senha gerada uma vez)
      python -m app.usuarios senha ana
      python -m app.usuarios listar
"""
import argparse
import getpass
import hashlib
import hmac
import os
import secrets
import sys
import time

ESQUEMA = """
CREATE TABLE IF NOT EXISTS usuarios (
    id         INTEGER PRIMARY KEY,
    nome       TEXT UNIQUE NOT NULL COLLATE NOCASE,
    senha_hash TEXT NOT NULL,
    papel      TEXT NOT NULL DEFAULT 'usuario' CHECK (papel IN ('admin','usuario')),
    pc         TEXT,                        -- chave do PC no config do worker de entrega (PCS)
    ativo      INTEGER NOT NULL DEFAULT 1,
    criado_em  REAL
);
CREATE TABLE IF NOT EXISTS sessoes (
    token      TEXT PRIMARY KEY,
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    criada_em  REAL,
    expira_em  REAL
);
"""
ITERACOES = 240_000
SESSAO_DIAS = 30


def preparar(con):
    con.executescript(ESQUEMA)


def hash_senha(senha: str, sal: bytes | None = None) -> str:
    sal = sal or os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", senha.encode(), sal, ITERACOES)
    return f"pbkdf2_sha256${ITERACOES}${sal.hex()}${h.hex()}"


def conferir_senha(senha: str, guardado: str) -> bool:
    try:
        _, it, sal, h = guardado.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", senha.encode(), bytes.fromhex(sal), int(it))
        return hmac.compare_digest(calc.hex(), h)
    except Exception:  # noqa: BLE001
        return False


def criar(con, nome: str, senha: str, admin: bool = False, pc: str | None = None) -> int:
    if len(senha) < 8:
        raise ValueError("senha precisa de 8+ caracteres")
    cur = con.execute("INSERT INTO usuarios(nome, senha_hash, papel, pc, criado_em) VALUES(?,?,?,?,?)",
                      (nome, hash_senha(senha), "admin" if admin else "usuario", pc, time.time()))
    con.commit()
    return cur.lastrowid


def trocar_senha(con, nome: str, senha: str):
    if len(senha) < 8:
        raise ValueError("senha precisa de 8+ caracteres")
    if not con.execute("UPDATE usuarios SET senha_hash=? WHERE nome=?", (hash_senha(senha), nome)).rowcount:
        raise ValueError("usuário não existe")
    con.execute("DELETE FROM sessoes WHERE usuario_id=(SELECT id FROM usuarios WHERE nome=?)", (nome,))
    con.commit()


def autenticar(con, nome: str, senha: str) -> dict | None:
    r = con.execute("SELECT * FROM usuarios WHERE nome=? AND ativo=1", (nome,)).fetchone()
    if r and conferir_senha(senha, r["senha_hash"]):
        return dict(r)
    hash_senha(senha)  # mesmo custo quando o usuário não existe
    return None


def abrir_sessao(con, usuario_id: int) -> str:
    token = secrets.token_urlsafe(32)
    agora = time.time()
    con.execute("DELETE FROM sessoes WHERE expira_em < ?", (agora,))
    con.execute("INSERT INTO sessoes VALUES(?,?,?,?)", (token, usuario_id, agora, agora + SESSAO_DIAS * 86400))
    con.commit()
    return token


def da_sessao(con, token: str | None) -> dict | None:
    if not token:
        return None
    r = con.execute("SELECT u.* FROM sessoes s JOIN usuarios u ON u.id=s.usuario_id "
                    "WHERE s.token=? AND s.expira_em>? AND u.ativo=1", (token, time.time())).fetchone()
    return dict(r) if r else None


def fechar_sessao(con, token: str):
    con.execute("DELETE FROM sessoes WHERE token=?", (token,))
    con.commit()


def listar(con):
    return [dict(r) for r in con.execute("SELECT id, nome, papel, pc, ativo FROM usuarios ORDER BY nome")]


def main(argv=None):
    from . import config
    from .indexer import store
    p = argparse.ArgumentParser(prog="python -m app.usuarios")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("criar")
    c.add_argument("nome")
    c.add_argument("--admin", action="store_true")
    c.add_argument("--pc")
    c.add_argument("--gerar", action="store_true", help="gera uma senha aleatória e mostra uma vez")
    s = sub.add_parser("senha")
    s.add_argument("nome")
    s.add_argument("--gerar", action="store_true")
    sub.add_parser("listar")
    a = p.parse_args(argv)
    con = store.conectar(config.BANCO)
    if a.cmd == "listar":
        for u in listar(con):
            print(f"{u['nome']:10} {u['papel']:8} pc={u['pc'] or '-'} {'ativo' if u['ativo'] else 'inativo'}")
        return 0
    senha = secrets.token_urlsafe(9) if a.gerar else getpass.getpass("Senha: ")
    if a.cmd == "criar":
        criar(con, a.nome, senha, a.admin, a.pc)
    else:
        trocar_senha(con, a.nome, senha)
    print(f"ok{f' — senha: {senha}' if a.gerar else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
