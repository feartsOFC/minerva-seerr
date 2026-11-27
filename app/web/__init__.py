"""Interface web (Jinja + HTMX + Alpine), no estilo "telemetria" do homepage.home."""
import os
import time
import urllib.parse

from fastapi import APIRouter, Cookie, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .. import config, pedidos, usuarios
from ..catalog import catalogo
from ..indexer import store
from ..meta import capas

AQUI = os.path.dirname(__file__)
tpl = Jinja2Templates(directory=os.path.join(AQUI, "templates"))
rota = APIRouter()

ROTULO_STATUS = {
    "aguardando_aprovacao": ("AGUARDANDO APROVAÇÃO", "amarelo"), "sem_espaco": ("SEM ESPAÇO NO SERVIDOR", "amarelo"),
    "na_fila": ("NA FILA", "apagado"), "baixando": ("BAIXANDO", "amarelo"), "pausado": ("PAUSADO", "amarelo"),
    "baixado": ("BAIXADO · ESPERANDO O PC", "amarelo"), "entregando": ("COPIANDO PARA O PC", "amarelo"),
    "entregue": ("DISPONÍVEL NO PC", "verde"), "erro": ("ERRO", "vermelho"), "cancelado": ("CANCELADO", "apagado"),
}


def tamanho(n) -> str:
    n = float(n or 0)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return (f"{n:.0f} {u}" if u in ("B", "KB") else f"{n:.1f} {u}").replace(".", ",")
        n /= 1024
    return str(n)


def quando(ts) -> str:
    if not ts:
        return "—"
    d = time.time() - ts
    if d < 60:
        return "AGORA"
    if d < 3600:
        return f"HÁ {int(d // 60)} MIN"
    if d < 86400:
        return f"HÁ {int(d // 3600)} H"
    return time.strftime("%d/%m %H:%M", time.localtime(ts))


tpl.env.filters.update(tamanho=tamanho, quando=quando, q=lambda s: urllib.parse.quote(str(s)))
tpl.env.globals.update(ROTULO_STATUS=ROTULO_STATUS)


def _con():
    return store.conectar(config.BANCO)


def _usuario(c, token):
    return usuarios.da_sessao(c, token)


def _pagina(request, nome, c, u, **ctx):
    return tpl.TemplateResponse(request, nome, {"u": u, "plats": catalogo.plataformas(c), **ctx})


def _login():
    return RedirectResponse("/login", 303)


# ---------------------------------------------------------------------------------------------- login

@rota.get("/login", response_class=HTMLResponse)
def tela_login(request: Request, erro: str = ""):
    return tpl.TemplateResponse(request, "login.html", {"erro": erro})


@rota.post("/login")
def fazer_login(request: Request, nome: str = Form(...), senha: str = Form(...)):
    c = _con()
    try:
        u = usuarios.autenticar(c, nome.strip(), senha)
        if not u:
            return tpl.TemplateResponse(request, "login.html", {"erro": "Usuário ou senha errados.", "nome": nome}, 401)
        token = usuarios.abrir_sessao(c, u["id"])
    finally:
        c.close()
    r = RedirectResponse("/", 303)
    seguro = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    r.set_cookie("minerva_sessao", token, max_age=usuarios.SESSAO_DIAS * 86400, httponly=True, samesite="lax", secure=seguro)
    return r


@rota.post("/sair")
def sair(minerva_sessao: str | None = Cookie(None)):
    if minerva_sessao:
        c = _con()
        try:
            usuarios.fechar_sessao(c, minerva_sessao)
        finally:
            c.close()
    r = RedirectResponse("/login", 303)
    r.delete_cookie("minerva_sessao")
    return r


# ---------------------------------------------------------------------------------------------- explorar

def em_alta(c, limite=12):
    """Mais pedidos nos últimos 30 dias; completa com os mais bem avaliados no IGDB que têm capa."""
    ids = [r[0] for r in c.execute(
        "SELECT jogo_id FROM pedidos WHERE criado_em > ? AND status != 'cancelado' GROUP BY jogo_id "
        "ORDER BY COUNT(*) DESC, MAX(criado_em) DESC LIMIT ?", (time.time() - 30 * 86400, limite))]
    if len(ids) < limite:
        ids += [r[0] for r in c.execute(
            "SELECT j.id FROM jogos j JOIN metadados m ON m.jogo_id=j.id WHERE m.nota IS NOT NULL AND j.so_extras=0 "
            "AND j.capa_fonte IS NOT NULL AND m.incerto=0 AND j.qtd_versoes>0 ORDER BY m.nota DESC, m.ano DESC LIMIT ?",
            (limite * 2,)) if r[0] not in ids][: limite - len(ids)]
    if not ids:
        return []
    marcas = ",".join("?" * len(ids))
    por_id = {r["id"]: dict(r) for r in c.execute(
        f"SELECT j.id, j.titulo, j.tamanho_princ, p.curto, m.ano, m.nota FROM jogos j JOIN plataformas p "
        f"ON p.id=j.plataforma_id LEFT JOIN metadados m ON m.jogo_id=j.id WHERE j.id IN ({marcas})", ids)}
    return [por_id[i] for i in ids if i in por_id]


@rota.get("/", response_class=HTMLResponse)
def explorar(request: Request, q: str = "", plataforma: int | None = None, extras: bool = False,
             minerva_sessao: str | None = Cookie(None)):
    c = _con()
    try:
        u = _usuario(c, minerva_sessao)
        if not u:
            return _login()
        jogos = catalogo.listar_jogos(c, q, plataforma, extras, 48, 0)
        return _pagina(request, "explorar.html", c, u, q=q, plataforma=plataforma, extras=extras, jogos=jogos,
                       alta=em_alta(c) if not q and not plataforma else [], offset=48, inicio=True, aba="explorar")
    finally:
        c.close()


@rota.get("/parcial/catalogo", response_class=HTMLResponse)
def parcial_catalogo(request: Request, q: str = "", plataforma: int | None = None, extras: bool = False,
                     offset: int = Query(0, ge=0), minerva_sessao: str | None = Cookie(None)):
    c = _con()
    try:
        if not _usuario(c, minerva_sessao):
            return HTMLResponse("", 401)
        jogos = catalogo.listar_jogos(c, q, plataforma, extras, 48, offset)
        return tpl.TemplateResponse(request, "_cards.html", {"jogos": jogos, "q": q, "plataforma": plataforma,
                                                             "extras": extras, "offset": offset + 48,
                                                             "inicio": offset == 0})
    finally:
        c.close()


# ---------------------------------------------------------------------------------------------- jogo

@rota.get("/jogo/{jogo_id}", response_class=HTMLResponse)
def pagina_jogo(request: Request, jogo_id: int, minerva_sessao: str | None = Cookie(None)):
    c = _con()
    try:
        u = _usuario(c, minerva_sessao)
        if not u:
            return _login()
        capas.enriquecer(c, jogo_id)
        j = catalogo.jogo(c, jogo_id)
        if not j:
            return HTMLResponse("jogo não existe", 404)
        meus = pedidos.do_jogo(c, jogo_id, u["id"])
        a = pedidos.ajustes(c)
        return _pagina(request, "jogo.html", c, u, j=j, meus=meus, limite=a["limite_gb"] * pedidos.GB, aba="explorar")
    finally:
        c.close()


@rota.post("/jogo/{jogo_id}/pedir", response_class=HTMLResponse)
def pedir(request: Request, jogo_id: int, versao: str = Form(""), extra: list[int] = Form(default=[]),
          minerva_sessao: str | None = Cookie(None)):
    c = _con()
    try:
        u = _usuario(c, minerva_sessao)
        if not u:
            return HTMLResponse("faça login", 401)
        erro = None
        try:
            pedidos.criar(c, u, jogo_id, versao, extras=extra)
        except pedidos.ErroPedido as e:
            erro = str(e)
        return tpl.TemplateResponse(request, "_status_jogo.html",
                                    {"meus": pedidos.do_jogo(c, jogo_id, u["id"]), "erro": erro, "jogo_id": jogo_id})
    finally:
        c.close()


@rota.get("/parcial/jogo/{jogo_id}/status", response_class=HTMLResponse)
def status_jogo(request: Request, jogo_id: int, minerva_sessao: str | None = Cookie(None)):
    c = _con()
    try:
        u = _usuario(c, minerva_sessao)
        if not u:
            return HTMLResponse("", 401)
        return tpl.TemplateResponse(request, "_status_jogo.html", {"meus": pedidos.do_jogo(c, jogo_id, u["id"]),
                                                                   "jogo_id": jogo_id})
    finally:
        c.close()


# ---------------------------------------------------------------------------------------------- solicitações

def _lista_pedidos(c, u, todos):
    ver_todos = u["papel"] == "admin" and todos
    lista = pedidos.listar(c, None if ver_todos else u["id"], 200)
    return lista, ver_todos


@rota.get("/solicitacoes", response_class=HTMLResponse)
def solicitacoes(request: Request, todos: bool = True, minerva_sessao: str | None = Cookie(None)):
    c = _con()
    try:
        u = _usuario(c, minerva_sessao)
        if not u:
            return _login()
        lista, ver_todos = _lista_pedidos(c, u, todos)
        return _pagina(request, "solicitacoes.html", c, u, lista=lista, todos=ver_todos, aba="solicitacoes")
    finally:
        c.close()


@rota.get("/parcial/solicitacoes", response_class=HTMLResponse)
def parcial_solicitacoes(request: Request, todos: bool = True, minerva_sessao: str | None = Cookie(None)):
    c = _con()
    try:
        u = _usuario(c, minerva_sessao)
        if not u:
            return HTMLResponse("", 401)
        lista, ver_todos = _lista_pedidos(c, u, todos)
        return tpl.TemplateResponse(request, "_lista_pedidos.html", {"lista": lista, "u": u, "todos": ver_todos})
    finally:
        c.close()


@rota.post("/solicitacoes/{pid}/{acao}", response_class=HTMLResponse)
def acao_pedido(request: Request, pid: int, acao: str, todos: bool = True, minerva_sessao: str | None = Cookie(None)):
    c = _con()
    try:
        u = _usuario(c, minerva_sessao)
        if not u:
            return HTMLResponse("", 401)
        p = pedidos.pedido(c, pid)
        if p and (p["usuario_id"] == u["id"] or u["papel"] == "admin"):
            try:
                if acao == "cancelar":
                    pedidos.cancelar(c, pid)
                elif acao == "aprovar" and u["papel"] == "admin":
                    pedidos.aprovar(c, pid, u)
                elif acao == "repetir" and p["status"] == "erro":
                    c.execute("UPDATE pedidos SET status='na_fila', erro=NULL WHERE id=?", (pid,))
                    c.commit()
                    pedidos.enviar(c, pid, pedidos.cliente(c))
            except pedidos.ErroPedido:
                pass
        lista, ver_todos = _lista_pedidos(c, u, todos)
        return tpl.TemplateResponse(request, "_lista_pedidos.html", {"lista": lista, "u": u, "todos": ver_todos})
    finally:
        c.close()


# ---------------------------------------------------------------------------------------------- ajustes

def _res(ok: bool, msg: str) -> HTMLResponse:
    import html
    return HTMLResponse(f'<span class="{"ok" if ok else "ruim"}">{"✓" if ok else "✗"} {html.escape(msg)}</span>')


@rota.get("/ajustes", response_class=HTMLResponse)
def ajustes(request: Request, minerva_sessao: str | None = Cookie(None)):
    c = _con()
    try:
        u = _usuario(c, minerva_sessao)
        if not u:
            return _login()
        ctx = {"aba": "ajustes"}
        if u["papel"] == "admin":
            a = capas.ajustes(c)
            ctx.update(ped=pedidos.ajustes(c), stats=store.stats(c),
                       igdb_id=bool(a.get("igdb_client_id")), igdb_ligado=bool(a.get("igdb")),
                       libretro_ligado=bool(a.get("libretro")), usuarios=usuarios.listar(c),
                       com_meta=c.execute("SELECT COUNT(*) FROM metadados").fetchone()[0],
                       com_capa=c.execute("SELECT COUNT(*) FROM jogos WHERE capa_fonte IS NOT NULL").fetchone()[0],
                       total_jogos=c.execute("SELECT COUNT(*) FROM jogos WHERE qtd_versoes>0").fetchone()[0])
        return _pagina(request, "ajustes.html", c, u, **ctx)
    finally:
        c.close()


@rota.post("/ajustes/senha", response_class=HTMLResponse)
def trocar_minha_senha(atual: str = Form(...), nova: str = Form(...), nova2: str = Form(...),
                       minerva_sessao: str | None = Cookie(None)):
    c = _con()
    try:
        u = _usuario(c, minerva_sessao)
        if not u:
            return _res(False, "faça login de novo")
        if nova != nova2:
            return _res(False, "as duas senhas novas não batem")
        if not usuarios.autenticar(c, u["nome"], atual):
            return _res(False, "senha atual errada")
        try:
            usuarios.trocar_senha(c, u["nome"], nova)
        except ValueError as e:
            return _res(False, str(e))
        token = usuarios.abrir_sessao(c, u["id"])  # trocar_senha derruba as sessões; esta continua logada
    finally:
        c.close()
    r = _res(True, "senha trocada; outras sessões foram desconectadas")
    r.set_cookie("minerva_sessao", token, max_age=usuarios.SESSAO_DIAS * 86400, httponly=True, samesite="lax")
    return r


def _admin(c, token):
    u = _usuario(c, token)
    return u if u and u["papel"] == "admin" else None


@rota.post("/ajustes/pedidos", response_class=HTMLResponse)
async def salvar_pedidos(request: Request, minerva_sessao: str | None = Cookie(None)):
    form = await request.form()
    c = _con()
    try:
        if not _admin(c, minerva_sessao):
            return _res(False, "só o admin")
        try:
            num = {"limite_gb": (1, 500), "pedidos_por_dia": (1, 100), "espaco_minimo_gb": (0, 2000)}
            for k, (lo, hi) in num.items():
                v = int(form.get(k, ""))
                if not lo <= v <= hi:
                    return _res(False, f"{k} fora de {lo}–{hi}")
                store.definir_ajuste(c, f"ped_{k}", v)
            dest = str(form.get("destino_pc", "")).strip().strip("/\\")
            if not dest or ".." in dest:
                return _res(False, "pasta de destino inválida")
            store.definir_ajuste(c, "ped_destino_pc", dest.replace("\\", "/"))
            store.definir_ajuste(c, "ped_aprovacao", form.get("aprovacao") == "on")
            url = str(form.get("qbit_url", "")).strip()
            if url and not url.startswith(("http://", "https://")):
                return _res(False, "URL do qBit precisa começar com http://")
            if url:
                store.definir_ajuste(c, "ped_qbit_url", url.rstrip("/"))
        except ValueError:
            return _res(False, "número inválido")
        return _res(True, "ajustes salvos")
    finally:
        c.close()


@rota.post("/ajustes/capas", response_class=HTMLResponse)
def salvar_capas(igdb_id: str = Form(""), igdb_secret: str = Form(""), libretro: str = Form(""), igdb: str = Form(""),
                 minerva_sessao: str | None = Cookie(None)):
    c = _con()
    try:
        if not _admin(c, minerva_sessao):
            return _res(False, "só o admin")
        store.definir_ajuste(c, "capas_libretro", libretro == "on")
        if igdb_id.strip():
            store.definir_ajuste(c, "igdb_client_id", igdb_id.strip())
        if igdb_secret.strip():
            store.definir_ajuste(c, "igdb_client_secret", igdb_secret.strip())
        store.definir_ajuste(c, "capas_igdb", igdb == "on")
        if igdb == "on":
            cli = capas.cliente_igdb(c)
            if not cli:
                return _res(False, "IGDB ligado mas sem Client ID/Secret")
            try:
                cli.validar()
            except Exception as e:  # noqa: BLE001
                return _res(False, f"salvo, mas a Twitch recusou: {e}")
            return _res(True, "salvo · chaves do IGDB válidas")
        return _res(True, "salvo")
    finally:
        c.close()
