"""CLI: ./nw <command>

  ./nw init                                   作業フォルダと目印を作る（本番は gocryptfs のマウント先で）
  ./nw serve [--host H] [--port 8766] [--open]
  ./nw kakera add|list|search|show|rm ...
  ./nw neta add|list|promote ...
  ./nw article add|list ...
  ./nw coverage 記事名
  ./nw check 下書き.md ... [--json]            警告を出すだけ（本文は変更しない）。指摘があれば終了コード1
  ./nw rules                                  ルールファイルの場所と検証結果

作業フォルダが使えない（未初期化・未マウント）ときは終了コード2。check と rules は作業フォルダなしでも動く。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import unicodedata
from pathlib import Path
from typing import List, Tuple

from . import store


def _kakera():
    from . import kakera
    return kakera


def _compliance():
    from . import compliance
    return compliance


def _read_body(parts: List[str]) -> str:
    if parts:
        return " ".join(parts)
    if sys.stdin.isatty():
        sys.exit("本文を引数で渡すか、標準入力から流し込んでください（例: echo 本文 | ./nw neta add）。")
    return sys.stdin.read()


def _width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)


def _pad(s: str, w: int, right: bool = False) -> str:
    gap = " " * max(0, w - _width(s))
    return gap + s if right else s + gap


def _table(header: List[str], rows: List[List[str]], right_from: int = 99) -> str:
    cols = [header] + rows
    widths = [max(_width(r[i]) for r in cols) for i in range(len(header))]
    out = []
    for n, r in enumerate(cols):
        out.append("  ".join(_pad(c, widths[i], right=i >= right_from and n > 0) for i, c in enumerate(r)).rstrip())
        if n == 0:
            out.append("  ".join("-" * w for w in widths))
    return "\n".join(out)


def _one_line(s: str, n: int = 40) -> str:
    t = " ".join((s or "").split())
    return t if len(t) <= n else t[:n] + "…"


def _list_opt(v) -> List[str]:
    return store.split_list(v or "")


# ---------- init / serve ----------

def cmd_init(args) -> None:
    d = store.data_dir()
    print("注意: 本番では、gocryptfs でマウントした復号ビュー（マウント先）を作業フォルダにして実行してください。")
    print("      例) gocryptfs ~/nw-cipher ~/nw-data && NW_DATA_DIR=~/nw-data ./nw init")
    print("      マウントしていない同名フォルダで init すると、平文のまま保存されてしまいます。")
    store.init_vault()
    print(f"作業フォルダを用意しました: {d}")
    if not os.path.ismount(str(d)):
        print("（参考）このフォルダはマウントポイントではありません。ダミーデータでの動作確認なら問題ありません。")
    print("実データ（ダミーでない事件の詳細）は、暗号化・ログ無害化・一時ファイル対策・スワップ対策・"
          "暗号化バックアップ・復元テストがすべて終わるまで入力しないでください。")


def cmd_serve(args) -> None:
    from . import web
    try:
        web.serve(host=args.host, port=args.port, open_browser=args.open)
    except web.HostRefused as ex:
        print(f"起動しません: {ex}", file=sys.stderr)
        sys.exit(2)


# ---------- かけら ----------

def _print_kakera_rows(items) -> None:
    if not items:
        print("（該当するかけらはありません）")
        return
    rows = [[k["id"], k["status"], " › ".join(x for x in (k["article"], k["section"]) if x) or "—",
             ",".join(k["viewpoints"]) or "—", _one_line(k["body"])] for k in items]
    print(_table(["ID", "状態", "記事 › 区間", "観点", "本文"], rows))
    print(f"\n{len(items)} 件")


def cmd_kakera_add(args) -> None:
    K = _kakera()
    body = _read_body(args.body)
    if not body.strip():
        sys.exit("本文が空です。")
    k = K.create_kakera(body, article=args.article, section=args.section, viewpoints=_list_opt(args.viewpoints),
                        status=args.status, tags=_list_opt(args.tags), source=args.source)
    print(f"かけら {k['id']} を保存しました")


def cmd_kakera_list(args) -> None:
    _print_kakera_rows(_kakera().list_kakera())


def cmd_kakera_search(args) -> None:
    items = _kakera().search_kakera(q=args.q, article=args.article, section=args.section, viewpoint=args.viewpoint,
                                    status=args.status, tag=args.tag)
    _print_kakera_rows(items)


def cmd_kakera_show(args) -> None:
    K = _kakera()
    k = K.get_kakera(args.id)
    for key, label in (("id", "ID"), ("article", "記事"), ("section", "区間"), ("status", "使用状況"),
                       ("source", "出どころ"), ("created", "作成"), ("updated", "更新")):
        print(f"{label}: {k.get(key, '')}")
    print(f"観点: {', '.join(k['viewpoints'])}")
    print(f"タグ: {', '.join(k['tags'])}")
    refs = K.delete_warnings(k["id"])
    if refs:
        print(f"生成来歴: {len(refs)} 件（{', '.join(r.get('id', '') for r in refs)}）")
    print("---")
    print(k["body"])


def _print_refs(refs) -> None:
    rows = [[r.get("id", ""), r.get("article", ""), r.get("target", ""), (r.get("generated_at") or "").replace("T", " ")]
            for r in refs]
    print(_table(["来歴ID", "記事", "段落・対象", "生成日時"], rows))


def cmd_kakera_rm(args) -> None:
    K = _kakera()
    k = K.get_kakera(args.id)
    refs = K.delete_warnings(k["id"])
    if refs:
        print(f"警告: かけら {k['id']} を根拠に生成した段落・記事が {len(refs)} 件あります。", file=sys.stderr)
        _print_refs(refs)
        if not args.force:
            print("削除しませんでした。それでも削除するなら --force を付けてください。", file=sys.stderr)
            sys.exit(1)
    try:
        K.delete_kakera(k["id"], force=args.force)
    except K.ReferencedError as ex:
        print(f"削除しませんでした: {ex}", file=sys.stderr)
        sys.exit(1)
    print(f"かけら {k['id']} を削除しました")


# ---------- ネタ帳 ----------

def cmd_neta_add(args) -> None:
    body = _read_body(args.body)
    if not body.strip():
        sys.exit("ネタが空です。")
    n = _kakera().create_neta(body, tags=_list_opt(args.tags))
    print(f"ネタ {n['id']} を保存しました")


def cmd_neta_list(args) -> None:
    items = _kakera().list_neta()
    if not args.all:
        items = [n for n in items if n["status"] == "未整理"]
    if not items:
        print("（該当するネタはありません）")
        return
    rows = [[n["id"], n["status"], n.get("kakera_id") or "—", ",".join(n["tags"]) or "—", _one_line(n["body"])]
            for n in items]
    print(_table(["ID", "状態", "かけら", "タグ", "本文"], rows))
    if not args.all:
        print("\n未整理のみ表示しています（すべて: --all）")


def cmd_neta_promote(args) -> None:
    k = _kakera().promote_neta(args.id, article=args.article, section=args.section,
                               viewpoints=_list_opt(args.viewpoints), tags=_list_opt(args.tags))
    print(f"ネタ {args.id} をかけら {k['id']} にしました")


# ---------- 記事 ----------

def cmd_article_add(args) -> None:
    a = _kakera().save_article(args.name, series=args.series, order=args.order, sections=_list_opt(args.sections))
    print(f"記事「{a['name']}」を保存しました（区間 {len(a['sections'])} 個）")


def cmd_article_list(args) -> None:
    arts = _kakera().list_articles()
    if not arts:
        print("（記事はまだありません）")
        return
    rows = [[a["name"], a["series"] or "—", str(a["order"]), " / ".join(a["sections"]) or "—"] for a in arts]
    print(_table(["記事", "シリーズ", "順番", "区間"], rows))


def cmd_coverage(args) -> None:
    cov = _kakera().coverage(args.article)
    vps = cov["viewpoints"]
    if not cov["sections"]:
        print(f"記事「{cov['article']}」の区間がありません（./nw article add で区間を設定してください）。")
    else:
        rows = []
        for s in cov["sections"]:
            cells = [str(s["counts"].get(v, 0)) if s["counts"].get(v, 0) else "0 !" for v in vps]
            pct = int(round(100 * s["filled"] / len(vps))) if vps else 0
            rows.append([s["section"]] + cells + [f"{pct}%"])
        print(f"充足度: {cov['article']}（「0 !」はまだかけらがない枠）\n")
        print(_table(["区間"] + vps + ["充足率"], rows, right_from=1))
    if cov.get("unsectioned"):
        print(f"\n区間が未設定のかけら: {cov['unsectioned']} 件")


# ---------- コンプライアンス ----------

SEVERITY_LABEL = {"strong": "強い警告", "warn": "警告", "info": "参考"}


def cmd_check(args) -> None:
    from .web import run_checks  # 画面と同じ手順（各文書＋シリーズ、重複除去）
    C = _compliance()
    docs: List[Tuple[str, str]] = []
    for f in args.files:
        try:
            text = Path(f).read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError) as ex:
            print(f"読めません: {f}: {ex}", file=sys.stderr)
            sys.exit(2)
        docs.append((f, text.replace("\r\n", "\n").replace("\r", "\n")))
    rules = C.load_rules()
    findings = run_checks(docs, rules)
    if args.json:
        print(json.dumps({"files": [n for n, _ in docs], "rules": rules.get("_sources", []),
                          "summary": C.summarize(findings), "findings": [f.to_dict() for f in findings]},
                         ensure_ascii=False, indent=2))
    else:
        print("警告は判断材料です。直すかどうかは人が決めます（本文は変更していません）。")
        for f in sorted(findings, key=lambda f: ([n for n, _ in docs].index(f.doc) if f.doc in [n for n, _ in docs] else 99,
                                                 f.line or 0)):
            sev = SEVERITY_LABEL.get(f.severity, f.severity)
            print(f"{f.doc}:{f.line}: [{sev}/{f.category}] 「{f.match}」 {f.message}")
        if findings:
            counts = {}
            for f in findings:
                counts[f.category] = counts.get(f.category, 0) + 1
            print("\n指摘 {} 件（{}）".format(len(findings), "、".join(f"{c} {n}" for c, n in counts.items())))
        else:
            print("ルールに当てはまる箇所は見つかりませんでした（見つからない＝問題がない、ではありません）。")
    sys.exit(1 if findings else 0)


def cmd_rules(args) -> None:
    C = _compliance()
    rules = C.load_rules()
    print("ルールファイル:")
    for s in rules.get("_sources", []):
        print(f"  {s}")
    problems = C.validate_rules(rules)
    if problems:
        print(f"NG: {len(problems)} 件の問題があります")
        for p in problems:
            print(f"  - {p}")
        sys.exit(1)
    print("OK")


# ---------- 引数 ----------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nw", description="notewriter: かけら管理とコンプライアンスチェッカー")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="作業フォルダと目印を作る").set_defaults(func=cmd_init)

    s = sub.add_parser("serve", help="Web画面を起動する")
    s.add_argument("--host", default="127.0.0.1", help="待ち受けアドレス（既定 127.0.0.1。スマホからは Tailscale のIP）")
    s.add_argument("--port", type=int, default=8766)
    s.add_argument("--open", action="store_true", help="ブラウザを開く")
    s.set_defaults(func=cmd_serve)

    k = sub.add_parser("kakera", help="かけら").add_subparsers(dest="sub", required=True)
    a = k.add_parser("add", help="かけらを追加（本文は引数か標準入力）")
    a.add_argument("body", nargs="*")
    a.add_argument("--article", default="")
    a.add_argument("--section", default="")
    a.add_argument("--viewpoints", default="", help="カンマ区切り（例: 五感,セリフ）")
    a.add_argument("--tags", default="", help="カンマ区切り")
    a.add_argument("--status", default="未使用")
    a.add_argument("--source", default="")
    a.set_defaults(func=cmd_kakera_add)
    k.add_parser("list", help="一覧").set_defaults(func=cmd_kakera_list)
    a = k.add_parser("search", help="検索（条件はAND）")
    for opt in ("q", "article", "section", "viewpoint", "status", "tag"):
        a.add_argument(f"--{opt}", default="")
    a.set_defaults(func=cmd_kakera_search)
    a = k.add_parser("show", help="1件表示")
    a.add_argument("id")
    a.set_defaults(func=cmd_kakera_show)
    a = k.add_parser("rm", help="削除（生成来歴に参照があれば --force が必要）")
    a.add_argument("id")
    a.add_argument("--force", action="store_true")
    a.set_defaults(func=cmd_kakera_rm)

    n = sub.add_parser("neta", help="ネタ帳").add_subparsers(dest="sub", required=True)
    a = n.add_parser("add", help="ネタを追加（本文は引数か標準入力）")
    a.add_argument("body", nargs="*")
    a.add_argument("--tags", default="")
    a.set_defaults(func=cmd_neta_add)
    a = n.add_parser("list", help="未整理のネタ一覧")
    a.add_argument("--all", action="store_true", help="かけら化済みも表示")
    a.set_defaults(func=cmd_neta_list)
    a = n.add_parser("promote", help="ネタをかけらにする")
    a.add_argument("id")
    a.add_argument("--article", default="")
    a.add_argument("--section", default="")
    a.add_argument("--viewpoints", default="")
    a.add_argument("--tags", default="")
    a.set_defaults(func=cmd_neta_promote)

    ar = sub.add_parser("article", help="記事と区間").add_subparsers(dest="sub", required=True)
    a = ar.add_parser("add", help="記事を追加・上書き")
    a.add_argument("name")
    a.add_argument("--series", default="")
    a.add_argument("--order", type=int, default=0)
    a.add_argument("--sections", default="", help="カンマ区切り（例: 逮捕の朝,取調べ,面会）")
    a.set_defaults(func=cmd_article_add)
    ar.add_parser("list", help="記事一覧").set_defaults(func=cmd_article_list)

    a = sub.add_parser("coverage", help="区間×観点の充足度")
    a.add_argument("article")
    a.set_defaults(func=cmd_coverage)

    a = sub.add_parser("check", help="下書きのコンプライアンスチェック（警告のみ）")
    a.add_argument("files", nargs="+")
    a.add_argument("--json", action="store_true", help="機械可読な出力")
    a.set_defaults(func=cmd_check)

    sub.add_parser("rules", help="ルールファイルの場所と検証結果").set_defaults(func=cmd_rules)
    return p


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except store.VaultError as ex:
        print(f"作業フォルダが使えません: {ex}", file=sys.stderr)
        sys.exit(2)
    except KeyError as ex:
        print(f"見つかりません: {ex.args[0] if ex.args else ex}", file=sys.stderr)
        sys.exit(1)
    except ValueError as ex:
        print(f"エラー: {ex}", file=sys.stderr)
        sys.exit(1)
    except BrokenPipeError:  # ./nw kakera list | head など
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())


if __name__ == "__main__":
    main()
