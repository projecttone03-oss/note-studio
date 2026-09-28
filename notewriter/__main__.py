"""CLI: ./nw <command>

  ./nw init                                   作業フォルダと目印を作る（本番は gocryptfs のマウント先で）
  ./nw serve [--host H] [--port 8766] [--open]
  ./nw kakera add|list|search|show|rm ...
  ./nw neta add|list|promote ...
  ./nw article add|list ...
  ./nw coverage 記事名
  ./nw check 下書き.md ... [--json]            警告を出すだけ（本文は変更しない）。指摘があれば終了コード1
  ./nw rules                                  ルールファイルの場所と検証結果
  ./nw research run --kind trend|deep [--provider P] [--article A] [質問文]
                                              送る全文・警告・費用の目安を表示し、yes と打ったときだけ送る
  ./nw research list|show|import|usage ...    リサーチ資料と今月の使用額
  ./nw research key set|status|delete         Perplexity のAPIキー（set は画面に出さずに入力）

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


# ---------- リサーチ ----------

def _research():
    from . import research
    return research


def _secrets():
    from . import secrets as nw_secrets
    return nw_secrets


def _safe(text) -> str:
    """エラーなどを出す前に、APIキーらしき文字列を伏せる。"""
    s = "" if text is None else str(text)
    try:
        s = _secrets().redact(s)
    except Exception:
        pass
    import re
    return re.sub(r"pplx-[A-Za-z0-9_\-]{6,}", "pplx-****", s)


def _yen(v) -> str:
    try:
        n = float(v or 0)
    except (TypeError, ValueError):
        return "—"
    if 0 < n < 1:
        return "1円未満"
    return f"{int(round(n)):,}円"


def _est_text(est) -> str:
    if isinstance(est, (int, float)):
        return f"だいたい {_yen(est)}"
    if not isinstance(est, dict) or not est.get("paid"):
        return "追加料金なし"
    lo, hi = _yen(est.get("jpy_low")), _yen(est.get("jpy_high"))
    return f"だいたい {hi}" if lo == hi else f"だいたい {lo}〜{hi}"


def _ask_yes(question: str, tty_only: bool) -> bool:
    """yes と打ったときだけ True。tty_only なら /dev/tty から読む（開けなければ中止）。"""
    if tty_only:
        try:
            tty_in = open("/dev/tty", "r", encoding="utf-8")
            tty_out = open("/dev/tty", "w", encoding="utf-8")
        except OSError:
            print("端末（/dev/tty）が開けないため、確認できません。送らずに終了します。", file=sys.stderr)
            return False
        with tty_in, tty_out:
            tty_out.write(question)
            tty_out.flush()
            answer = tty_in.readline()
    else:
        try:
            answer = input(question)
        except EOFError:
            answer = ""
    return answer.strip().lower() == "yes"


def cmd_research_run(args) -> None:
    R, S = _research(), _secrets()
    if args.query:
        if not sys.stdin.isatty():
            sys.exit("標準入力が端末ではないため、実行しません（有料の操作を自動化しないため）。端末から実行してください。")
        query, from_stdin = " ".join(args.query), False
    else:
        if sys.stdin.isatty():
            sys.exit("質問文を引数で渡すか、標準入力から流し込んでください。")
        query, from_stdin = sys.stdin.read(), True
    if not query.strip():
        sys.exit("質問文が空です。")
    provider = args.provider or R.load_config().get("default_provider") or "claude"
    if args.kind == "deep" and not args.article:
        sys.exit("深掘り調査は --article で記事を指定してください。")
    if R.is_paid(provider) and not S.has_api_key():
        sys.exit("Perplexity のAPIキーが未登録です。./nw research key set で登録してください。")
    p = R.prepare(args.kind, provider, query, article=args.article)
    print(f"種類: {R.KINDS.get(p['kind'], p['kind'])}")
    print(f"送り先: {p.get('provider_label') or R.provider_label(provider)}")
    print(f"記事: {p.get('article') or '記事なし'}")
    print("\n===== 外に送る文章（これが全文です） =====")
    print(p["prompt"])
    print("===== ここまで =====\n")
    warns = p.get("warnings") or []
    if warns:
        print(f"気をつける語が {len(warns)} 件あります（止めるかどうかはあなたが決めます）:")
        for w in warns:
            line = f"{w.get('line')}行目 " if w.get("line") else ""
            print(f"  - {line}[{w.get('category')}] 「{w.get('match')}」 {w.get('message')}")
        print()
    usage = R.month_usage()
    if R.is_paid(provider):
        est = p.get("estimate") or {}
        print(f"費用: {_est_text(est)}（目安）")
        if est.get("pricing_note"):
            print(f"      {est['pricing_note']}")
        after = (usage.get("total_jpy") or 0) + (est.get("jpy_high") or 0)
        print(f"今月の使用額: {_yen(usage.get('total_jpy'))} ／ 上限 {_yen(usage.get('budget_jpy'))}"
              f"（実行後の見込み 最大 {_yen(after)}）")
    else:
        print("費用: 追加料金なし（Claude の契約の利用枠を使います）")
    if not p.get("budget_ok", True):
        print(f"送れません: {_safe(p.get('budget_message'))}", file=sys.stderr)
        sys.exit(1)
    if not _ask_yes("この内容で送りますか？ 送るなら yes と入力: ", tty_only=from_stdin):
        print("送りませんでした。")
        sys.exit(1)
    print("調べています（数分かかることがあります）…", flush=True)
    try:
        job = R.run_job(p["kind"], provider, p.get("query", query), p.get("article", args.article), p["confirm_token"])
    except R.BudgetExceeded as ex:
        print(f"送りませんでした: {_safe(ex)}", file=sys.stderr)
        sys.exit(1)
    status = job.get("status")
    if status == "done":
        print(f"完了しました。リサーチ資料 {job.get('material_id')} に保存しました（./nw research show {job.get('material_id')}）。")
    elif status == "limit":
        pe = job.get("estimate_jpy_for_pplx")
        if not pe:
            pe = R.estimate("pplx_standard", p["kind"])
        resets = job.get("limit_resets_at")
        print(f"Claude の利用上限に達しました{'（' + str(resets).replace('T', ' ') + 'ごろ解除）' if resets else ''}。"
              f"Perplexity で続けますか？（目安{_est_text(pe).replace('だいたい ', '')}）")
        print("続けるときは、--provider pplx_standard を付けて改めて実行してください（自動では続けません）。")
        sys.exit(3)
    else:
        print(f"うまくいきませんでした: {_safe(job.get('error') or '理由は分かりませんでした')}", file=sys.stderr)
        sys.exit(1)


def _provider_name(key) -> str:
    try:
        return _research().provider_label(key) if key in _research().PROVIDER_KEYS else (key or "—")
    except Exception:
        return key or "—"


def cmd_research_list(args) -> None:
    items = sorted(_research().list_materials(args.article or None),
                   key=lambda m: (m.get("researched_at") or "", m.get("id") or ""), reverse=True)
    if not items:
        print("（リサーチ資料はありません）")
        return
    rows = [[str(m.get("id")), m.get("researched_at") or "—", ("1年以上前" if m.get("stale") else ""),
             m.get("provider_label") or _provider_name(m.get("provider")), m.get("model") or "—", m.get("article") or "—",
             str(len(m.get("sources") or [])), _one_line(m.get("title") or m.get("query") or "", 30)] for m in items]
    print(_table(["ID", "調べた日", "古さ", "担当", "モデル", "記事", "出典", "題"], rows))
    print(f"\n{len(items)} 件")


def cmd_research_show(args) -> None:
    m = _research().get_material(args.id)
    for key, label in (("id", "ID"), ("title", "題"), ("article", "記事"), ("kind", "種類"), ("model", "モデル"),
                       ("researched_at", "調べた日"), ("origin", "取り込み")):
        print(f"{label}: {m.get(key) or '—'}")
    print(f"担当: {m.get('provider_label') or _provider_name(m.get('provider'))}")
    if m.get("cost_jpy") not in (None, ""):
        print(f"費用: {_yen(m.get('cost_jpy'))}")
    if m.get("stale"):
        print(f"注意: 1年以上前の資料です（{m.get('age_days')}日前）。使う前に最新の情報で確認し直してください。")
    print("注意: 体験談の記事では、書き方・背景の参考にとどめます。本文に新しい事実として混ぜないでください。")
    srcs = m.get("sources") or []
    print(f"出典: {len(srcs)} 件")
    for s in srcs:
        print(f"  - {s.get('title') or ''} {s.get('url') or ''} {s.get('date') or ''}".rstrip())
    print("---")
    print(m.get("body", ""))


def cmd_research_import(args) -> None:
    R = _research()
    if args.date:
        from datetime import datetime
        try:
            datetime.strptime(args.date, "%Y-%m-%d")
        except ValueError:
            sys.exit("--date は YYYY-MM-DD の形で指定してください。")
    for f in args.files:
        try:
            text = Path(f).read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError) as ex:
            print(f"読めません: {f}: {ex}", file=sys.stderr)
            sys.exit(2)
        m = R.import_material(Path(f).name, text.replace("\r\n", "\n").replace("\r", "\n"), article=args.article,
                              researched_at=args.date or None)
        print(f"{f} → リサーチ資料 {m.get('id')} として取り込みました")


def cmd_research_usage(args) -> None:
    u = _research().month_usage(args.month or None)
    runs = u.get("runs") or []
    if runs:
        rows = [[(r.get("at") or "").replace("T", " "), _provider_name(r.get("provider")), r.get("model") or "—",
                 r.get("kind") or "—", "不明" if r.get("cost_unknown") else _yen(r.get("cost_jpy")),
                 r.get("cost_source") or "—"] for r in runs]
        print(_table(["日時", "担当", "モデル", "種類", "金額", "算出"], rows, right_from=4))
        print()
    else:
        print("（この月の実行はありません）")
    print(f"{u.get('month')} の使用額: {_yen(u.get('total_jpy'))} ／ 上限 {_yen(u.get('budget_jpy'))}"
          f"（残り {_yen(u.get('remaining_jpy'))}）")
    if u.get("unknown_count"):
        print(f"金額不明の実行: {u['unknown_count']} 件（目安 最大 {_yen(u.get('unknown_estimated_jpy'))} として上限に数えています）")
    print("上限は config/research.json（作業フォルダ側で上書き可）の monthly_budget_jpy で変えられます。")


def cmd_research_key_set(args) -> None:
    import getpass
    key = getpass.getpass("Perplexity のAPIキー（入力しても表示されません）: ")
    try:
        warns = _secrets().set_api_key(key)
    finally:
        key = ""
    print("APIキーを登録しました。")
    for w in warns:
        print(f"注意: {_safe(w)}")


def cmd_research_key_status(args) -> None:
    src = _secrets().api_key_source()
    print({"file": "登録済み", "env": "登録済み（環境変数で設定済み）"}.get(src, "未登録"))


def cmd_research_key_delete(args) -> None:
    print("APIキーを削除しました。" if _secrets().delete_api_key() else "削除するキーはありませんでした。")


# ---------- 引数 ----------

class _Parser(argparse.ArgumentParser):
    """引数の誤りを表示するときも、APIキーらしき文字列は伏せる（例: key set の後ろにキーを書いてしまったとき）。"""

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(2, _safe(f"{self.prog}: エラー: {message}") + "\n")


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(prog="nw", description="notewriter: かけら管理とコンプライアンスチェッカー")
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

    r = sub.add_parser("research", help="リサーチ").add_subparsers(dest="sub", required=True)
    a = r.add_parser("run", help="調べる（送る全文を表示し、yes と打ったときだけ送る）")
    a.add_argument("query", nargs="*", help="質問文（省略すると標準入力から読む）")
    a.add_argument("--kind", required=True, choices=("trend", "deep"))
    a.add_argument("--provider", choices=("claude", "pplx_standard", "pplx_deep"), default=None,
                   help="担当（省略すると設定の default_provider）")
    a.add_argument("--article", default="")
    a.set_defaults(func=cmd_research_run)
    a = r.add_parser("list", help="リサーチ資料の一覧")
    a.add_argument("--article", default="")
    a.set_defaults(func=cmd_research_list)
    a = r.add_parser("show", help="リサーチ資料を1件表示")
    a.add_argument("id")
    a.set_defaults(func=cmd_research_show)
    a = r.add_parser("import", help="Markdown を手動で取り込む")
    a.add_argument("files", nargs="+")
    a.add_argument("--article", default="")
    a.add_argument("--date", default="", help="調べた日（YYYY-MM-DD。省略すると今日）")
    a.set_defaults(func=cmd_research_import)
    a = r.add_parser("usage", help="今月の使用額")
    a.add_argument("--month", default="", help="YYYY-MM")
    a.set_defaults(func=cmd_research_usage)
    kk = r.add_parser("key", help="Perplexity のAPIキー").add_subparsers(dest="keycmd", required=True)
    kk.add_parser("set", help="登録（画面に出さずに入力）").set_defaults(func=cmd_research_key_set)
    kk.add_parser("status", help="登録済みか").set_defaults(func=cmd_research_key_status)
    kk.add_parser("delete", help="登録したキーを削除").set_defaults(func=cmd_research_key_delete)
    return p


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except store.VaultError as ex:
        print(f"作業フォルダが使えません: {ex}", file=sys.stderr)
        sys.exit(2)
    except KeyError as ex:
        print(_safe(f"見つかりません: {ex.args[0] if ex.args else ex}"), file=sys.stderr)
        sys.exit(1)
    except ValueError as ex:
        print(_safe(f"エラー: {ex}"), file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n中止しました。", file=sys.stderr)
        sys.exit(130)
    except Exception as ex:  # リサーチは外部とやり取りするので、トレースバックではなく伏せ字済みの理由だけ出す
        if getattr(args, "cmd", "") != "research":
            raise
        print(_safe(f"エラー（{type(ex).__name__}）: {ex}"), file=sys.stderr)
        sys.exit(1)
    except BrokenPipeError:  # ./nw kakera list | head など
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())


if __name__ == "__main__":
    main()
