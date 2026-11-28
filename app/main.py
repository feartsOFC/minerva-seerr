"""API do minerva-seerr (fase 1: índice, busca e magnets)."""
import os
import threading
import time

import hmac
import logging

from fastapi import BackgroundTasks, Body, Cookie, Depends, FastAPI, Header, HTTPException, Query, Request, Response as Resp
from fastapi.responses import FileResponse, Response

from . import config, pedidos, usuarios
from .catalog import catalogo
from .indexer import store, update
from .meta import capas

app = FastAPI(title="minerva-seerr", version="0.4.0")
from fastapi.staticfiles import StaticFiles  # noqa: E402
from . import web  # noqa: E402
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "web", "static")), name="static")
app.include_router(web.rota)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
SESSAO = "minerva_sessao"
WORKER_TOKEN = os.environ.get("MINERVA_WORKER_TOKEN", "")
_trava_atualizacao = threading.Lock()
_estado = {"rodando": False, "etapa": None, "n": 0, "total": 0, "erro": None}


def _con():
    os.makedirs(os.path.dirname(os.path.abspath(config.BANCO)), exist_ok=True)
    return store.conectar(config.BANCO)


def usuario_atual(minerva_sessao: str | None = Cookie(None)) -> dict:
    c = _con()
    try:
        u = usuarios.da_sessao(c, minerva_sessao)
    finally:
        c.close()
    if not u:
        raise HTTPException(401, "faça login")
    return u


def admin_atual(u: dict = Depends(usuario_atual)) -> dict:
    if u["papel"] != "admin":
        raise HTTPException(403, "só o admin")
    return u


def _publico(u: dict) -> dict:
    return {k: u[k] for k in ("id", "nome", "papel", "pc")}


@app.get("/api/saude")
def saude():
    c = _con()
    try:
        return {"ok": True, **store.stats(c)}
    finally:
        c.close()


@app.get("/api/colecoes")
def listar_colecoes():
    c = _con()
    try:
        return store.colecoes(c)
    finally:
        c.close()


@app.get("/api/busca")
def busca(q: str = Query(..., min_length=1, max_length=200), colecao: str | None = None,
          limite: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
    c = _con()
    try:
        return {"q": q, "itens": store.buscar(c, q, colecao=colecao, limite=limite, offset=offset)}
    finally:
        c.close()


@app.get("/api/arquivos/{colecao_id}/{idx}")
def detalhe_arquivo(colecao_id: int, idx: int, trackers: int = Query(config.TRACKERS_NO_MAGNET, ge=0)):
    c = _con()
    try:
        a = store.arquivo(c, colecao_id, idx)
        if not a:
            raise HTTPException(404, "arquivo não existe nesse torrent (ou é padding)")
        mag = store.magnet_de(a, trackers)
        a.pop("trackers", None)
        a.pop("arquivo_local", None)
        return {**a, "so": idx, "magnet": mag}
    finally:
        c.close()


def _rodar_atualizacao(forcar: bool):
    c = _con()
    try:
        def prog(etapa, n, total):
            _estado.update(etapa=etapa, n=n, total=total)
        update.atualizar(c, config.PASTA_TORRENTS, config.INDICE_URL, config.COLECOES, forcar=forcar, progresso=prog)
        _estado.update(etapa="capas", n=0, total=0)
        capas.casar_libretro(c)
        _estado["erro"] = None
    except Exception as e:  # noqa: BLE001
        _estado["erro"] = str(e)
    finally:
        c.close()
        _estado["rodando"] = False
        _trava_atualizacao.release()


@app.post("/api/indexador/atualizar", status_code=202, dependencies=[Depends(admin_atual)])
def atualizar_indice(tarefas: BackgroundTasks, forcar: bool = False):
    if not _trava_atualizacao.acquire(blocking=False):
        raise HTTPException(409, "atualização já em andamento")
    _estado.update(rodando=True, etapa="varrendo", n=0, total=0, erro=None)
    tarefas.add_task(_rodar_atualizacao, forcar)
    return _estado


@app.get("/api/indexador")
def estado_indexador():
    c = _con()
    try:
        return {**_estado, "ultima": store.ajuste(c, "ultima_atualizacao"), "stats": store.stats(c),
                "colecoes_filtro": config.COLECOES or "todas"}
    finally:
        c.close()


# --------------------------------------------------------------------------------------- catálogo (fase 2)

@app.get("/api/plataformas")
def listar_plataformas():
    c = _con()
    try:
        return catalogo.plataformas(c)
    finally:
        c.close()


@app.get("/api/jogos")
def listar_jogos(q: str = Query("", max_length=200), plataforma: int | None = None, extras: bool = False,
                 ordem: str = Query("titulo", pattern="^(titulo|nota|ano)$"),
                 limite: int = Query(60, ge=1, le=200), offset: int = Query(0, ge=0)):
    c = _con()
    try:
        return {"itens": catalogo.listar_jogos(c, q, plataforma, extras, limite, offset, ordem)}
    finally:
        c.close()


@app.get("/api/jogos/{jogo_id}")
def detalhe_jogo(jogo_id: int):
    c = _con()
    try:
        capas.enriquecer(c, jogo_id)  # IGDB na primeira visita (se ligado); depois vem do cache
        j = catalogo.jogo(c, jogo_id)
        if not j:
            raise HTTPException(404, "jogo não existe")
        return j
    finally:
        c.close()


@app.get("/capas/{jogo_id}")
def capa(jogo_id: int):
    c = _con()
    try:
        arq = capas.obter(c, jogo_id)
        if arq:
            return FileResponse(arq, headers={"Cache-Control": "public, max-age=604800"})
        r = c.execute("SELECT j.titulo, p.curto FROM jogos j JOIN plataformas p ON p.id=j.plataforma_id WHERE j.id=?",
                      (jogo_id,)).fetchone()
        if not r:
            raise HTTPException(404)
        return Response(capas.placeholder(r[0], r[1]), media_type="image/svg+xml",
                        headers={"Cache-Control": "public, max-age=3600"})
    finally:
        c.close()


@app.post("/api/capas/casar", dependencies=[Depends(admin_atual)])
def casar_capas():
    c = _con()
    try:
        return capas.casar_libretro(c)
    finally:
        c.close()


@app.post("/api/capas/igdb/validar", dependencies=[Depends(admin_atual)])
def validar_igdb():
    c = _con()
    try:
        cli = capas.cliente_igdb(c)
        if not cli:
            raise HTTPException(400, "IGDB desligado ou sem Client ID/Secret")
        try:
            return cli.validar()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, str(e)) from e
    finally:
        c.close()


# --------------------------------------------------------------------------------------- usuários (fase 3)

@app.post("/api/login")
def login(request: Request, resposta: Resp, nome: str = Body(...), senha: str = Body(...)):
    c = _con()
    try:
        u = usuarios.autenticar(c, nome, senha)
        if not u:
            raise HTTPException(401, "usuário ou senha errados")
        token = usuarios.abrir_sessao(c, u["id"])
    finally:
        c.close()
    resposta.set_cookie(SESSAO, token, max_age=usuarios.SESSAO_DIAS * 86400, httponly=True, samesite="lax",
                        secure=request.url.scheme == "https")  # https direto ou via proxy (MINERVA_PROXY_IPS)
    return _publico(u)


@app.post("/api/logout")
def logout(resposta: Resp, minerva_sessao: str | None = Cookie(None)):
    if minerva_sessao:
        c = _con()
        try:
            usuarios.fechar_sessao(c, minerva_sessao)
        finally:
            c.close()
    resposta.delete_cookie(SESSAO)
    return {"ok": True}


@app.get("/api/eu")
def eu(u: dict = Depends(usuario_atual)):
    return _publico(u)


# --------------------------------------------------------------------------------------- pedidos (fase 3)

@app.post("/api/pedidos", status_code=201)
def novo_pedido(jogo_id: int = Body(...), versao: str = Body(...), u: dict = Depends(usuario_atual)):
    c = _con()
    try:
        return pedidos.criar(c, u, jogo_id, versao)
    except pedidos.ErroPedido as e:
        raise HTTPException(400, str(e)) from e
    finally:
        c.close()


@app.get("/api/pedidos")
def meus_pedidos(todos: bool = False, u: dict = Depends(usuario_atual)):
    c = _con()
    try:
        return pedidos.listar(c, None if (todos and u["papel"] == "admin") else u["id"])
    finally:
        c.close()


@app.get("/api/pedidos/{pid}")
def ver_pedido(pid: int, u: dict = Depends(usuario_atual)):
    c = _con()
    try:
        p = pedidos.pedido(c, pid)
        if not p or (p["usuario_id"] != u["id"] and u["papel"] != "admin"):
            raise HTTPException(404)
        return p
    finally:
        c.close()


@app.post("/api/pedidos/{pid}/cancelar")
def cancelar_pedido(pid: int, u: dict = Depends(usuario_atual)):
    c = _con()
    try:
        p = pedidos.pedido(c, pid)
        if not p or (p["usuario_id"] != u["id"] and u["papel"] != "admin"):
            raise HTTPException(404)
        pedidos.cancelar(c, pid)
        return pedidos.pedido(c, pid)
    except pedidos.ErroPedido as e:
        raise HTTPException(400, str(e)) from e
    finally:
        c.close()


@app.post("/api/pedidos/{pid}/aprovar")
def aprovar_pedido(pid: int, u: dict = Depends(admin_atual)):
    c = _con()
    try:
        pedidos.aprovar(c, pid, u)
        return pedidos.pedido(c, pid)
    finally:
        c.close()


@app.get("/api/qbit/teste")
def testar_qbit(u: dict = Depends(admin_atual)):
    c = _con()
    try:
        qb = pedidos.cliente(c)
        try:
            return {**qb.versao(), "espaco_livre": qb.espaco_livre()}
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, str(e)) from e
    finally:
        c.close()


# --------------------------------------------------------------------------------------- worker do host

def worker_autorizado(x_minerva_token: str | None = Header(None)):
    if not WORKER_TOKEN or not x_minerva_token or not hmac.compare_digest(x_minerva_token, WORKER_TOKEN):
        raise HTTPException(401, "token do worker inválido")


@app.get("/api/worker/entregas", dependencies=[Depends(worker_autorizado)])
def worker_entregas():
    c = _con()
    try:
        return pedidos.entregas_pendentes(c)
    finally:
        c.close()


@app.post("/api/worker/entregas/{pid}", dependencies=[Depends(worker_autorizado)])
def worker_relatar(pid: int, status: str = Body(...), mensagem: str | None = Body(None), destino: str | None = Body(None)):
    c = _con()
    try:
        return pedidos.relatar_entrega(c, pid, status, mensagem, destino, pedidos.cliente(c))
    except pedidos.ErroPedido as e:
        raise HTTPException(400, str(e)) from e
    finally:
        c.close()


# --------------------------------------------------------------------------------------- laço de status

def _laco_status():
    while True:
        try:
            c = _con()
            try:
                if c.execute("SELECT 1 FROM pedidos WHERE status IN ('na_fila','baixando','pausado','sem_espaco','aguardando_vez') "
                             "LIMIT 1").fetchone():
                    pedidos.sincronizar(c, pedidos.cliente(c))
            finally:
                c.close()
        except Exception as e:  # noqa: BLE001
            logging.getLogger("minerva.status").warning("sincronização falhou: %s", e)
        time.sleep(20)


@app.on_event("startup")
def _iniciar_laco():
    if os.environ.get("MINERVA_SEM_LACO") != "1":
        threading.Thread(target=_laco_status, daemon=True, name="status").start()


# --------------------------------------------------------------------------------------- configuração inicial
# Link de uso único (30 min) para colar chaves e definir senhas pelo navegador, sem terminal.
# Gerar:  docker exec minerva-seerr python -m app.configurar   (o endereço vem de MINERVA_URL no .env)
# Numa instalação nova (sem usuários) o mesmo link cria o primeiro admin.

import hashlib
import html as _html
import secrets as _secrets
import sqlite3

from fastapi import Form, Request  # noqa: F401
from fastapi.responses import HTMLResponse


def gerar_link_config(minutos: int = 30) -> str:
    alfabeto = "abcdefghjkmnpqrstuvwxyz23456789"  # sem 0/o, 1/l/i: dá para digitar sem errar
    tok = "-".join("".join(_secrets.choice(alfabeto) for _ in range(4)) for _ in range(3))
    c = _con()
    try:
        store.definir_ajuste(c, "config_token", {"hash": hashlib.sha256(tok.encode()).hexdigest(),
                                                  "expira": time.time() + minutos * 60})
    finally:
        c.close()
    return f"{os.environ.get('MINERVA_URL', 'http://localhost:8000').rstrip('/')}/c/{tok}"


def _token_ok(c, t: str) -> bool:
    d = store.ajuste(c, "config_token")
    return bool(d and t and time.time() < d["expira"]
                and hmac.compare_digest(hashlib.sha256(t.encode()).hexdigest(), d["hash"]))


_CSS = """<style>
:root{--fundo:#0a0a0a;--celula:#0f0f0f;--linha:#262626;--texto:#e8e6e1;--apagado:#75726c;--amarelo:#ffd23f;--verde:#4af626;--vermelho:#ff2a2a}
*{box-sizing:border-box}body{margin:0;background:var(--fundo);color:var(--texto);font:15px/1.5 "JetBrains Mono",ui-monospace,monospace;padding:16px}
main{max-width:520px;margin:6vh auto;border:1px solid var(--linha);background:var(--celula);padding:22px}
h1{font:800 30px/1 "Archivo","Helvetica Neue",Arial,sans-serif;letter-spacing:-.01em;margin:0 0 4px;text-transform:uppercase}
.rot{color:var(--apagado);font-size:11px;letter-spacing:.14em;text-transform:uppercase;margin:22px 0 8px;border-top:1px solid var(--linha);padding-top:14px}
label{display:block;font-size:12px;color:var(--apagado);margin:10px 0 4px}
input{width:100%;background:#141414;border:1px solid var(--linha);color:var(--texto);padding:11px;font:inherit;border-radius:0}
input:focus{outline:2px solid var(--amarelo);outline-offset:-1px}
button{margin-top:22px;width:100%;background:var(--amarelo);color:#0a0a0a;border:0;padding:13px;font:800 14px "JetBrains Mono",monospace;letter-spacing:.1em;text-transform:uppercase;cursor:pointer}
.ok{color:var(--verde)}.erro{color:var(--vermelho)}p{margin:6px 0}small{color:var(--apagado)}
</style>"""


@app.get("/c/{t}", response_class=HTMLResponse)
def pagina_config_curta(t: str):
    return pagina_config(t.strip().lower())


def _campos_usuarios(c) -> str:
    lista = usuarios.listar(c)
    if not lista:
        return ("<div class=\"rot\">Primeiro administrador</div>"
                "<p><small>Ainda não há usuários. Crie o admin (senha com 8+ caracteres).</small></p>"
                "<label>Usuário</label><input name=\"novo_nome\" autocapitalize=\"none\" spellcheck=\"false\">"
                "<label>Senha</label><input name=\"novo_senha\" type=\"password\" autocomplete=\"new-password\">")
    return ("<div class=\"rot\">Senhas (mínimo 8 caracteres)</div>" + "".join(
        f"<label>{_html.escape(u['nome'])} <small>· {u['papel']}</small></label>"
        f"<input name=\"senha_{u['id']}\" type=\"password\" autocomplete=\"new-password\">" for u in lista))


@app.get("/configurar", response_class=HTMLResponse)
def pagina_config(t: str = ""):
    c = _con()
    try:
        if not _token_ok(c, t):
            return HTMLResponse(f"<!doctype html><meta name=viewport content='width=device-width'>{_CSS}<main><h1>Link vencido</h1>"
                                "<p>Peça um link novo.</p></main>", 403)
        campos = _campos_usuarios(c)
    finally:
        c.close()
    return f"""<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Minerva · configurar</title>{_CSS}<main><small>MINERVA · CONFIGURAÇÃO</small><h1>Chaves e senhas</h1>
<p><small>Link de uso único. Campo vazio = não muda.</small></p>
<form method="post" action="/configurar" autocomplete="off"><input type="hidden" name="t" value="{_html.escape(t)}">
{campos}
<div class="rot">IGDB · app da Twitch (opcional)</div>
<label>Client ID <small>(vazio = mantém o já salvo)</small></label><input name="igdb_id" spellcheck="false">
<label>Client Secret</label><input name="igdb_secret" type="password" spellcheck="false">
<button>Salvar</button></form></main></html>"""


@app.post("/configurar", response_class=HTMLResponse)
async def salvar_config(request: Request):
    form = await request.form()
    t = str(form.get("t", "")).strip().lower()
    igdb_id, igdb_secret = str(form.get("igdb_id", "")), str(form.get("igdb_secret", ""))
    c = _con()
    linhas = []
    try:
        if not _token_ok(c, t):
            return HTMLResponse(f"<!doctype html>{_CSS}<main><h1>Link vencido</h1></main>", 403)
        store.definir_ajuste(c, "config_token", None)  # uso único
        lista = usuarios.listar(c)
        novo_nome, novo_senha = str(form.get("novo_nome", "")).strip(), str(form.get("novo_senha", ""))
        if not lista and novo_nome and novo_senha:
            try:
                usuarios.criar(c, novo_nome, novo_senha, admin=True)
                linhas.append(f"<p class=ok>✓ admin {_html.escape(novo_nome)} criado — entre em /login</p>")
            except (ValueError, sqlite3.IntegrityError) as e:
                linhas.append(f"<p class=erro>✗ {_html.escape(str(e))}</p>")
        for u in lista:
            senha = str(form.get(f"senha_{u['id']}", ""))
            if senha:
                try:
                    usuarios.trocar_senha(c, u["nome"], senha)
                    linhas.append(f"<p class=ok>✓ senha de {_html.escape(u['nome'])} definida</p>")
                except ValueError as e:
                    linhas.append(f"<p class=erro>✗ {_html.escape(u['nome'])}: {_html.escape(str(e))}</p>")
        if igdb_secret.strip() and not igdb_id.strip():  # só o secret: reaproveita o Client ID já salvo
            igdb_id = store.ajuste(c, "igdb_client_id", "") or ""
        if igdb_id.strip() and igdb_secret.strip():
            store.definir_ajuste(c, "igdb_client_id", igdb_id.strip())
            store.definir_ajuste(c, "igdb_client_secret", igdb_secret.strip())
            store.definir_ajuste(c, "capas_igdb", True)
            try:
                capas.cliente_igdb(c).validar()
                linhas.append("<p class=ok>✓ IGDB: chaves válidas (token da Twitch obtido)</p>")
            except Exception as e:  # noqa: BLE001
                linhas.append(f"<p class=erro>✗ IGDB recusou: {_html.escape(str(e))}</p>")
        elif igdb_id.strip() or igdb_secret.strip():
            linhas.append("<p class=erro>✗ IGDB: preencha os dois campos</p>")
    finally:
        c.close()
    return (f"<!doctype html><html lang=pt-BR><meta charset=utf-8><meta name=viewport content='width=device-width'>{_CSS}"
            f"<main><small>MINERVA</small><h1>Pronto</h1>{''.join(linhas) or '<p>Nada mudou.</p>'}"
            "<p><small>Este link já não vale mais. Pode fechar.</small></p></main></html>")
