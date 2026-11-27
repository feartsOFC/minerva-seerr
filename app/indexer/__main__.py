"""CLI do indexador (mesmos comandos do minerva_index.py original).

  python3 -m app.indexer atualizar [--todas] [--forcar]
  python3 -m app.indexer buscar guitar hero --sistema "xbox 360" [--so-magnet] [--trackers N] [--saida x.txt]
  python3 -m app.indexer exportar --sistema "Xbox 360" --saida x.txt
  python3 -m app.indexer links --saida links.txt
  python3 -m app.indexer stats
"""
import argparse
import os
import sys
import time

from .. import config
from . import store, update
from .torrent import nome_colecao


def humano(n: float) -> str:
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n} B" if u == "B" else f"{n:.1f} {u}"
        n /= 1024
    return str(n)


def main(argv=None):
    p = argparse.ArgumentParser(prog="python3 -m app.indexer", description="Índice local do Minerva Archive")
    p.add_argument("--banco", default=config.BANCO)
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("atualizar")
    a.add_argument("--indice", default=config.INDICE_URL)
    a.add_argument("--conexoes", type=int, default=4)
    a.add_argument("--forcar", action="store_true")
    a.add_argument("--todas", action="store_true", help="ignora o filtro de coleções (catálogo inteiro)")
    b = sub.add_parser("buscar")
    b.add_argument("termos", nargs="+")
    b.add_argument("--sistema")
    b.add_argument("--limite", type=int, default=100)
    b.add_argument("--so-magnet", action="store_true")
    b.add_argument("--saida")
    b.add_argument("--trackers", type=int, default=config.TRACKERS_NO_MAGNET)
    e = sub.add_parser("exportar")
    e.add_argument("--sistema")
    e.add_argument("--saida", default="minerva_magnets.txt")
    e.add_argument("--trackers", type=int, default=config.TRACKERS_NO_MAGNET)
    lk = sub.add_parser("links")
    lk.add_argument("--indice", default=config.INDICE_URL)
    lk.add_argument("--saida", default="minerva_links.txt")
    sub.add_parser("stats")
    args = p.parse_args(argv)

    os.makedirs(os.path.dirname(os.path.abspath(args.banco)), exist_ok=True)
    con = store.conectar(args.banco)

    if args.cmd == "atualizar":
        def prog(etapa, n, total):
            print(f"\r  {etapa}: {n}/{total}   ", end="", flush=True)
        r = update.atualizar(con, config.PASTA_TORRENTS, args.indice, None if args.todas else config.COLECOES,
                             args.conexoes, args.forcar, prog)
        print(f"\n{r['selecionadas']} de {r['no_indice']} coleções · baixadas {r['baixadas']} · "
              f"reindexadas {r['reindexadas']} · removidas {r['removidas']} · {r['segundos']} s")
        for f in r["falhas"][:20]:
            print(f"  FALHA {f['url']}: {f['erro']}", file=sys.stderr)
        return 1 if r["falhas"] else 0

    if args.cmd == "buscar":
        linhas = store.buscar(con, " ".join(args.termos), colecao=args.sistema, limite=args.limite)
        if not linhas:
            print("Nenhum resultado.")
            return 0
        saida = open(args.saida, "w", encoding="utf-8") if args.saida else None
        atual = None
        for l in linhas:
            mag = store.magnet_de(store.arquivo(con, l["colecao_id"], l["idx"]), args.trackers)
            if args.so_magnet:
                print(mag)
                continue
            if l["colecao"] != atual:
                atual = l["colecao"]
                print(f"\n== {atual}")
            print(f"   {l['nome']}  ({humano(l['tamanho'])})  so={l['idx']}\n     {mag}")
            if saida:
                saida.write(f"{l['nome']}\t{humano(l['tamanho'])}\t{l['colecao']}\t{mag}\n")
        if saida:
            saida.close()
        return 0

    if args.cmd == "exportar":
        sql = ("SELECT a.colecao_id, a.idx FROM arquivos a JOIN colecoes c ON c.id=a.colecao_id"
               + (" WHERE c.nome LIKE ?" if args.sistema else "") + " ORDER BY c.nome, a.idx")
        n = 0
        with open(args.saida, "w", encoding="utf-8") as f:
            f.write("# Arquivo\tTamanho\tColeção\tMagnet\n")
            for cid, idx in con.execute(sql, [f"%{args.sistema}%"] if args.sistema else []).fetchall():
                l = store.arquivo(con, cid, idx)
                f.write(f"{l['nome']}\t{humano(l['tamanho'])}\t{l['colecao']}\t{store.magnet_de(l, args.trackers)}\n")
                n += 1
        print(f"{n} magnets salvos em {os.path.abspath(args.saida)}")
        return 0

    if args.cmd == "links":
        urls = update.varrer_indice(args.indice)
        with open(args.saida, "w", encoding="utf-8") as f:
            f.write(f"# Minerva Archive — {len(urls)} torrents — {time.strftime('%d/%m/%Y %H:%M')}\n")
            for nome, u in sorted((nome_colecao(u), u) for u in urls):
                f.write(f"{nome}\t{u}\n")
        print(f"{len(urls)} links salvos em {os.path.abspath(args.saida)}")
        return 0

    s = store.stats(con)
    print(f"Coleções: {s['colecoes']}\nArquivos: {s['arquivos']}\nTamanho total: {humano(s['tamanho_total'])}")
    if s["ultima_atualizacao"]:
        print("Última atualização: " + time.strftime("%d/%m/%Y %H:%M", time.localtime(s["ultima_atualizacao"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
