#!/usr/bin/env python3
"""minerva-seerr — worker de entrega (roda no host Proxmox, cron a cada 5 min).

Para cada pedido BAIXADO no app:
  - respeita as faixas bloqueadas (ponta da tarifa, backup) do worker do torrentbot;
  - liga o PC do dono por WoL se estiver desligado;
  - copia SÓ os arquivos do pedido (SSH + tar.exe do Windows) para <pasta de torrents do PC>/Jogos/<plataforma>
    (DLCs em .../DLC/<jogo>, title updates em .../Updates/<jogo>), confere o tamanho de cada um;
  - avisa o app (que tira o arquivo do torrent, ou apaga o torrent se ninguém mais usa) e apaga do disco do
    servidor o que o app liberou;
  - desliga o PC se foi o worker que ligou; avisa no #pedidos-torrent pelo bot do torrentbot.

Reaproveita as funções do worker do pedidos-torrent (mesmo config.json dos PCs) e usa o MESMO lock no cron, para
os dois nunca ligarem/desligarem o mesmo PC ao mesmo tempo.

Uso: minerva_entrega.py [--config /etc/minerva-seerr/worker.json] [--teste]
     --teste mostra o que faria, sem copiar, apagar, avisar nem mudar status.
"""
import importlib.util
import json
import os
import subprocess
import sys
import time
import urllib.request

ARGS = sys.argv[1:]
TESTE = "--teste" in ARGS
CFG = json.load(open(ARGS[ARGS.index("--config") + 1] if "--config" in ARGS else "/etc/minerva-seerr/worker.json"))
APP = CFG["app_url"].rstrip("/")
ESTADO = CFG.get("estado", "/var/lib/minerva-seerr/estado.json")
LOG = CFG.get("log", "/var/log/minerva-entrega.log")

# worker do torrentbot como biblioteca (lê o config dos PCs, faixas, ssh, WoL, avisar)
_spec = importlib.util.spec_from_file_location("torrent_destino", CFG.get("base", "/usr/local/bin/pedidos-torrent-worker"))
base = importlib.util.module_from_spec(_spec)
sys.argv = [sys.argv[0], "--config", CFG.get("pcs_config", "/etc/pedidos-torrent/config.json")] + (["--teste"] if TESTE else [])
_spec.loader.exec_module(base)


def log(msg):
    linha = time.strftime("%F %T ") + "minerva-entrega: " + msg
    print(linha, flush=True)
    if not TESTE:
        with open(LOG, "a") as f:
            f.write(linha + "\n")


def app(caminho, corpo=None):
    req = urllib.request.Request(APP + caminho, data=json.dumps(corpo).encode() if corpo is not None else None,
                                 headers={"X-Minerva-Token": CFG["token"], "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read() or b"null")


def relatar(pid, status, mensagem=None, destino=None):
    if TESTE:
        print(f"  [app] #{pid} → {status} {mensagem or ''} {destino or ''}")
        return {}
    return app(f"/api/worker/entregas/{pid}", {"status": status, "mensagem": mensagem, "destino": destino}) or {}


def avisar(pid, texto):
    base.avisar(f"minerva-{pid}", texto)  # hash desconhecido → o bot posta no canal, sem responder a nada


def gb(n):
    return f"{n / 1e9:.1f} GB".replace(".", ",")


def local_do_arquivo(raiz, qbit_nome, tamanho):
    """O arquivo pode estar na pasta final ou na temporária do qBit (se o torrent ainda baixa outros)."""
    t = None
    for a in ARQ_TORRENT.get(qbit_nome, []):
        t = a
    if not t:
        return None, "torrent não está no qBit"
    if t["state"] in ("moving", "checkingDL", "checkingUP"):
        return None, "qBit ainda está movendo/conferindo"
    for pasta in (t.get("save_path"), t.get("download_path")):
        if not pasta:
            continue
        p = os.path.join(raiz + pasta.rstrip("/"), qbit_nome)
        if os.path.isfile(p) and os.path.getsize(p) == tamanho:
            return p, None
    return None, "arquivo não encontrado no servidor com o tamanho certo"


ARQ_TORRENT: dict = {}


def copiar(pc, arquivos_host, destino):
    """arquivos_host = [(caminho_no_host, tamanho)]. Copia para `destino` (pasta no PC) e confere os tamanhos."""
    def remotos():
        r = base.ps(pc, f"[IO.Directory]::CreateDirectory('{destino}') | Out-Null; "  # New-Item do PS 5.1 não tem -LiteralPath
                        f"Get-ChildItem -File -LiteralPath '{destino}' | % {{ $_.Name + '|' + $_.Length }}")
        if r.returncode:
            raise RuntimeError(r.stderr.strip()[-200:])
        return {l.rsplit("|", 1)[0]: int(l.rsplit("|", 1)[1]) for l in r.stdout.splitlines() if "|" in l}

    for _ in range(3):
        ja = remotos()
        faltam = [(p, s) for p, s in arquivos_host if ja.get(os.path.basename(p)) != s]
        if not faltam:
            return
        for p, s in faltam:
            # leitor_tar: pausa os torrents / limita a leitura no host do homelab (modo cópia), senão só nice
            leitor = subprocess.Popen(base.leitor_tar(["tar", "--format=pax", "-cf", "-", "-C", os.path.dirname(p),
                                                       os.path.basename(p)], "minerva"), stdout=subprocess.PIPE)
            r = base.ssh(pc, f'tar -xf - -C "{destino}"', entrada=leitor.stdout, timeout=None)
            leitor.stdout.close()
            leitor.wait()
            if r.returncode or leitor.returncode:
                log(f"erro copiando {os.path.basename(p)}: tar {leitor.returncode}, ssh {r.returncode} {r.stderr.strip()[-150:]}")
                time.sleep(30)
    ja = remotos()
    erradas = [os.path.basename(p) for p, s in arquivos_host if ja.get(os.path.basename(p)) != s]
    if erradas:
        raise RuntimeError(f"{len(erradas)} arquivo(s) não conferem no PC (ex.: {erradas[0]})")


def main():
    try:
        est = json.load(open(ESTADO))
    except Exception:  # noqa: BLE001
        est = {}

    def primeira_vez(pid, chave):
        e = est.setdefault(str(pid), {})
        if e.get(chave):
            return False
        e[chave] = time.time()
        return True

    if base.MODO_COPIA and not TESTE:
        base.MODO_COPIA.limpar()
    pend = app("/api/worker/entregas")
    if not pend:
        return
    hashes = {a["infohash"] for p in pend for a in p["arquivos"]}
    for t in json.loads(base.qbit("/torrents/info?hashes=" + "|".join(hashes))):
        for p in pend:
            for a in p["arquivos"]:
                if a["infohash"] == t["hash"]:
                    ARQ_TORRENT.setdefault(a["qbit_nome"], []).append(t)
    raiz = base.raiz_midia()
    acordados = set()

    for p in pend:
        pid, pc = p["id"], base.PCS.get(p["pc"] or "")
        rotulo = f"**{p['titulo']}** ({p['plataforma']} · {p['versao']})"
        if not pc:
            log(f"#{pid}: usuário {p['usuario']} sem PC configurado")
            if primeira_vez(pid, "sem_pc"):
                relatar(pid, "erro", f"o usuário {p['usuario']} não tem PC configurado")
            continue
        motivo = base.bloqueado(p["tamanho"] / base.VELOCIDADE + 600)
        if motivo:
            log(f"#{pid}: esperando ({motivo}) — {p['titulo']}")
            if primeira_vez(pid, "esperando"):
                relatar(pid, "baixado", f"vai para o {pc['nome']} depois do {motivo}")
                if not TESTE:
                    avisar(pid, f"🕔 {rotulo}, pedido por {p['usuario']}, já baixou e vai para o {pc['nome']} depois do {motivo}.")
            continue
        locais, problema = [], None
        for a in p["arquivos"]:
            caminho, problema = local_do_arquivo(raiz, a["qbit_nome"], a["tamanho"])
            if not caminho:
                break
            locais.append((caminho, a["tamanho"], a.get("subpasta") or ""))
        if problema:
            log(f"#{pid}: {problema} — {p['titulo']}")
            if "movendo" not in problema and primeira_vez(pid, "sumiu"):
                relatar(pid, "erro", problema)
                if not TESTE:
                    avisar(pid, f"❌ {rotulo}, pedido por {p['usuario']}: {problema}. Peça de novo pelo minerva.home.")
            continue
        log(f"#{pid}: copiar {p['titulo']} ({gb(p['tamanho'])}) → {pc['nome']}")
        if TESTE:
            continue
        try:
            relatar(pid, "entregando")
            if p["pc"] not in acordados and not base.online(pc):
                log(f"{pc['nome']} desligado → WoL")
                if not base.acordar(pc):
                    raise RuntimeError(f"o {pc['nome']} não ligou pelo WoL em 6 min")
                acordados.add(p["pc"])
            pasta = base.escolher_destino(pc, p["tamanho"]).rstrip("/") + "/" + p["destino_relativo"]
            t0 = time.time()
            for sub in sorted({x[2] for x in locais}):  # jogo na pasta da plataforma; DLC/Updates em subpastas
                copiar(pc, [(c, t) for c, t, s in locais if s == sub], pasta + ("/" + sub if sub else ""))
            r = relatar(pid, "entregue", destino=pasta)
            for nome in r.get("apagar_arquivos", []):  # torrent ainda tem outros pedidos: apaga só este arquivo
                for t in ARQ_TORRENT.get(nome, []):
                    for d in (t.get("save_path"), t.get("download_path")):
                        if d and os.path.isfile(os.path.join(raiz + d.rstrip("/"), nome)):
                            os.remove(os.path.join(raiz + d.rstrip("/"), nome))
            mins = (time.time() - t0) / 60
            janela = pasta.replace("/", "\\")
            avisar(pid, f"💾 {rotulo}, pedido por {p['usuario']}, está no {pc['nome']} em `{janela}`"
                        + (" (DLC/atualizações nas subpastas DLC e Updates)" if any(x[2] for x in locais) else "") + " "
                        f"({len(locais)} arquivo(s), {gb(p['tamanho'])}, {mins:.0f} min, tamanhos conferidos). Apagado do servidor.")
            log(f"#{pid}: ok em {mins:.0f} min → {pasta}")
            est.pop(str(pid), None)
        except Exception as e:  # noqa: BLE001
            log(f"#{pid}: falhou: {e}")
            relatar(pid, "baixado", f"cópia falhou, tento de novo em 5 min: {str(e)[:200]}")
            if primeira_vez(pid, "erro_copia"):
                avisar(pid, f"❌ Não consegui copiar {rotulo} para o {pc['nome']}: {str(e)[:300]}\n"
                            "Continua no servidor; tento de novo a cada 5 min (sem repetir este aviso).")

    if base.MODO_COPIA and not TESTE:
        base.MODO_COPIA.fim()  # retoma os torrents antes de desligar os PCs
    for chave in acordados:
        base.ssh(base.PCS[chave], "shutdown.exe /s /t 120", timeout=60)
        log(f"{base.PCS[chave]['nome']}: desligando (foi ligado pelo worker)")

    if not TESTE:
        os.makedirs(os.path.dirname(ESTADO), exist_ok=True)
        vivos = {str(p["id"]) for p in pend}
        json.dump({k: v for k, v in est.items() if k in vivos}, open(ESTADO, "w"))


if __name__ == "__main__":
    main()
