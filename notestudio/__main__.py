"""CLI: ./ns <command>

Claude Code が使うコマンド: validate / import-research / import-plan / show / context
人間が使うコマンド:        serve / themes / decide / approve-plan / sync

書き込み系のコマンドは、目印ファイル .ns-local があるマシン（あなたのMac）でだけ動く。
クラウド（Claude Code on the web）では読み取りと validate だけができ、結果は inbox/ にJSONで置く。
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from . import db, export, models, schemas

RAW_DIR = db.DATA_DIR / "raw"
INBOX = db.ROOT / "inbox"
IMPORTED = INBOX / "imported"
CLOUD_MSG = ("この環境はクラウド（.ns-local がない）なので「{}」は実行できません。\n"
             "結果のJSONを inbox/ に保存して commit・push し、Macで ./ns sync を実行して取り込んでください。")


def require_local(action: str) -> None:
    if not db.is_local():
        sys.exit(CLOUD_MSG.format(action))


def _conn():
    """Macでは読み書き、クラウドでは読み取り専用でDBを開く。"""
    return db.connect(readonly=not db.is_local())


def _load_json(path: str) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.exit(f"JSONとして読めません: {path}: {e}")


def _archive(path: str, kind: str) -> str:
    """取り込んだ元JSONを data/raw/ に保存して後から検証できるようにする。"""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    dest = RAW_DIR / f"{datetime.now():%Y%m%d-%H%M%S}_{kind}_{Path(path).name}"
    shutil.copy(path, dest)
    return str(dest.relative_to(db.DATA_DIR))


def cmd_validate(args) -> None:
    data = _load_json(args.file)
    errors = schemas.validate_research(data) if args.kind == "research" else schemas.validate_plan(data)
    if errors:
        print(f"NG: {len(errors)}件の問題があります", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)
    print("OK")


def _import_file(conn, path: str, kind: str) -> str:
    """JSONを1件取り込み、結果メッセージを返す。失敗時は ValueError / CheckpointError。"""
    data = _load_json(path)
    if kind == "research":
        run_id, theme_ids = models.import_research(conn, data, _archive(path, "research"))
        for tid in theme_ids:
            export.write_theme(conn, tid)
        export.write_run(conn, run_id)
        return (f"R{run_id:03d} を取り込みました（候補 {len(theme_ids)} 件: "
                f"{', '.join(f'T{t:03d}' for t in theme_ids)}）")
    plan_id = models.import_plan(conn, data, _archive(path, "plan"))
    export.write_plan(conn, plan_id)
    export.write_theme(conn, data["theme_id"])
    return f"企画 P{plan_id:03d}（T{data['theme_id']:03d}）を取り込みました"


def _import_and_file_away(conn, path: str, kind: str) -> str:
    msg = _import_file(conn, path, kind)
    src = Path(path).resolve()
    if src.parent == INBOX.resolve():  # inbox/ 直下のファイルは取り込み済みフォルダへ移す
        IMPORTED.mkdir(exist_ok=True)
        src.rename(IMPORTED / src.name)
    return msg


def cmd_import_research(args) -> None:
    require_local("import-research")
    try:
        print(_import_and_file_away(_conn(), args.file, "research"))
    except ValueError as e:
        sys.exit(f"取り込み失敗:\n{e}")
    print("→ 次は人間の確認です。./ns serve で画面を開き、各テーマを 採用／保留／却下 してください。")


def cmd_import_plan(args) -> None:
    require_local("import-plan")
    try:
        print(_import_and_file_away(_conn(), args.file, "plan"))
    except models.CheckpointError as e:
        sys.exit(f"チェックポイント未通過: {e}")
    except ValueError as e:
        sys.exit(f"取り込み失敗:\n{e}")
    print("→ 次は人間の確認です。画面でタイトル・構成・価格を選んで『企画を承認』してください。")


def cmd_show(args) -> None:
    conn = _conn()
    theme = models.theme_detail(conn, args.theme_id)
    if theme is None:
        sys.exit(f"T{args.theme_id} が見つかりません")
    print(export.render_theme(theme))
    for p in theme["plans"]:
        if p["status"] != "superseded" or args.all:
            plan = models.get_plan(conn, p["id"])
            print("\n---\n")
            print(export.render_plan(plan, theme))


def cmd_context(args) -> None:
    print(json.dumps(models.research_context(_conn()), ensure_ascii=False, indent=2))


def cmd_themes(args) -> None:
    conn = _conn()
    rows = models.list_themes(conn, status=args.status)
    if not rows:
        print("テーマはまだありません")
    for t in rows:
        print(f"T{t['id']:03d}  {t['score_total']:5.1f}  [{models.THEME_STATUS[t['status']]}]  {t['title']}")


def cmd_decide(args) -> None:
    require_local("decide")
    conn = db.connect()
    models.decide_theme(conn, args.theme_id, args.action, args.note)
    export.refresh_theme(conn, args.theme_id)
    t = models.get_theme(conn, args.theme_id)
    print(f"T{t['id']:03d} → {models.THEME_STATUS[t['status']]}")


def cmd_approve_plan(args) -> None:
    require_local("approve-plan")
    conn = db.connect()
    plan = models.get_plan(conn, args.plan_id)
    if plan is None:
        sys.exit(f"P{args.plan_id} が見つかりません")
    title = args.title or plan["titles"][args.title_index - 1]["text"]
    price = args.price or plan["price"]["suggested"]
    try:
        models.approve_plan(conn, args.plan_id, title, args.outline_index - 1, price, args.note)
    except (ValueError, models.CheckpointError) as e:
        sys.exit(str(e))
    export.refresh_theme(conn, plan["theme_id"])
    print(f"P{args.plan_id:03d} を承認しました: {title} / {price}円")


def cmd_export(args) -> None:
    require_local("export")
    n = export.export_all(db.connect())
    print(f"{n} テーマ分のMarkdownを書き出しました → {db.DATA_DIR}")


def _git(*a: str, check: bool = False) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", *a], cwd=db.ROOT, capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.exit(f"git {' '.join(a)} に失敗しました:\n{r.stderr.strip()}")
    return r


def _commit_all(label: str) -> None:
    _git("add", "-A", check=True)
    if _git("diff", "--cached", "--quiet").returncode != 0:
        _git("commit", "-m", f"sync: {datetime.now():%Y-%m-%d %H:%M} {label}", check=True)


def _collect_cloud_results() -> list[str]:
    """クラウドが claude/... ブランチに置いた inbox/*.json のうち、未取り込みのものを main 側へコピーする。

    ブランチ自体はマージしない（inbox 以外の変更＝プログラムの書き換え等は取り込まない）。
    """
    notes = []
    for ref in _git("branch", "-r", "--format=%(refname:short)").stdout.split():
        if "/claude/" not in ref:
            continue
        base = _git("merge-base", "HEAD", ref).stdout.strip()
        if not base:
            continue
        changed = _git("diff", "--name-status", base, ref).stdout.splitlines()
        others = [line.split("\t")[-1] for line in changed if not line.split("\t")[-1].startswith("inbox/")]
        received = 0
        for line in changed:
            status, path = line.split("\t")[0], line.split("\t")[-1]
            name = Path(path).name
            if status != "A" or not path.startswith("inbox/") or "/" in path[len("inbox/"):] or not name.endswith(".json"):
                continue
            if (INBOX / name).exists() or (IMPORTED / name).exists():
                continue
            (INBOX / name).write_bytes(subprocess.run(["git", "show", f"{ref}:{path}"], cwd=db.ROOT,
                                                      capture_output=True).stdout)
            notes.append(f"  受け取り: {name}（{ref}）")
            received += 1
        if others and received:  # 新しく受け取ったときだけ注意を出す（毎回は出さない）
            notes.append(f"  注意: {ref} には inbox 以外の変更もありますが、取り込んでいません: {', '.join(others[:5])}")
    return notes


def cmd_sync(args) -> None:
    """クラウドの結果を受け取り → 取り込み → Markdown更新 → GitHubへ保存、を1回で行う。"""
    require_local("sync")
    has_remote = bool(_git("remote").stdout.strip())
    if has_remote:
        print("① GitHubから最新を受け取っています…")
        _commit_all("Macでの変更（採用・承認など）")  # 先に手元の変更を保存しておくと pull が止まらない
        _git("pull", "--no-rebase", "--no-edit", check=True)
        _git("fetch", "--prune", check=True)
        for note in _collect_cloud_results():
            print(note)
    else:
        print("① GitHubが未設定のため、受け取りはスキップします")

    print("② inbox/ のJSONを取り込んでいます…")
    conn = db.connect()
    files = sorted(INBOX.glob("*.json"), key=lambda f: (not f.name.startswith("research"), f.name))
    ok = 0
    for f in files:
        kind = "research" if f.name.startswith("research") else "plan" if f.name.startswith("plan") else None
        if kind is None:
            print(f"  スキップ: {f.name}（research_ か plan_ で始まるファイルだけ取り込みます）")
            continue
        try:
            print("  " + _import_and_file_away(conn, str(f), kind))
            ok += 1
        except models.CheckpointError as e:
            print(f"  保留: {f.name} — チェックポイント未通過: {e}")
        except ValueError as e:
            print(f"  失敗: {f.name} — {str(e).splitlines()[0]}（./ns validate で詳細を確認）")
    if not files:
        print("  新しいファイルはありません")
    export.export_all(conn)

    if has_remote:
        print("③ GitHubへ保存しています…")
        _commit_all(f"取り込み {ok} 件")
        _git("push", check=True)
        print("  保存しました")
    print(f"完了: {ok} 件を取り込みました。確認は ./ns serve で。")


def cmd_serve(args) -> None:
    require_local("serve")
    from . import web
    web.serve(args.port, open_browser=not args.no_browser)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="ns", description="note studio")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="確認用のローカル画面を起動")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-browser", action="store_true")
    s.set_defaults(func=cmd_serve)

    s = sub.add_parser("validate", help="JSONを検証（取り込みはしない）")
    s.add_argument("kind", choices=["research", "plan"])
    s.add_argument("file")
    s.set_defaults(func=cmd_validate)

    s = sub.add_parser("import-research", help="需要リサーチ結果を取り込む")
    s.add_argument("file")
    s.set_defaults(func=cmd_import_research)

    s = sub.add_parser("import-plan", help="企画ドラフトを取り込む（採用済みテーマのみ）")
    s.add_argument("file")
    s.set_defaults(func=cmd_import_plan)

    s = sub.add_parser("show", help="テーマと企画の詳細を表示")
    s.add_argument("theme_id", type=int)
    s.add_argument("--all", action="store_true", help="旧版の企画も表示")
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("context", help="次のリサーチ用に既出テーマと判断ログをJSONで出力")
    s.set_defaults(func=cmd_context)

    s = sub.add_parser("themes", help="テーマ一覧")
    s.add_argument("--status", choices=list(models.THEME_STATUS))
    s.set_defaults(func=cmd_themes)

    s = sub.add_parser("decide", help="【人間用】テーマを採用／保留／却下")
    s.add_argument("theme_id", type=int)
    s.add_argument("action", choices=list(models.DECISIONS))
    s.add_argument("--note")
    s.set_defaults(func=cmd_decide)

    s = sub.add_parser("approve-plan", help="【人間用】企画を承認")
    s.add_argument("plan_id", type=int)
    s.add_argument("--title-index", type=int, default=1)
    s.add_argument("--title", help="タイトルを自分で書く場合")
    s.add_argument("--outline-index", type=int, default=1)
    s.add_argument("--price", type=int)
    s.add_argument("--note")
    s.set_defaults(func=cmd_approve_plan)

    s = sub.add_parser("export", help="全データのMarkdownを書き出し直す")
    s.set_defaults(func=cmd_export)

    s = sub.add_parser("sync", help="【Mac用】クラウドの結果を受け取り→取り込み→GitHubへ保存")
    s.set_defaults(func=cmd_sync)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
