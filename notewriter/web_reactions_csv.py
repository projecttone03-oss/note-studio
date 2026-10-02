"""反応記録の CSV 取り込みの画面（/reactions/import）。取り込む前に読み取れた中身を見せ、押したときだけ記録する。"""
from __future__ import annotations

from datetime import date

from . import reactions_csv as RC
from .web import e

PREFIXES = ("/reactions/import",)


def page_upload() -> str:
    return f"""<p class="small"><a href="/reactions">反応記録</a></p><h1>CSV から取り込む</h1>
<div class="note">note などから書き出した CSV を取り込みます。<b>取り込む前に、読み取れた中身を確認</b>できます。
列名が合わないときは <code>config/reactions_csv.json</code> に列名を足してください。</div>
<div class="card"><h2>ファイルを選ぶ</h2><form method="post" action="/reactions/import/preview" enctype="multipart/form-data">
<label class="drop">ここに CSV をドラッグ&ドロップ<br><span class="small">（または押して選ぶ。UTF-8 か Shift_JIS）</span>
<input type="file" name="file" accept=".csv,text/csv" required data-drop><span class="dropnames"></span></label>
<label class="f">記録日（数えた日）</label><input type="date" name="recorded" value="{date.today().isoformat()}">
<div class="btnrow"><button class="btn primary">読み取って確認する</button></div></form></div>"""


def page_preview(text: str, recorded: str) -> str:
    r = RC.parse(text, recorded)
    rows = "".join(f'<tr><td>{e(x["title"])}</td><td>{e(x["published"] or "—")}</td><td class="num">{x["likes"]}</td>'
                   f'<td class="num">{x["comments"]}</td><td class="num">{x["purchases"]}</td><td class="small">{e(x["memo"])}</td></tr>'
                   for x in r["rows"][:200])
    errs = "".join(f"<li>{n}行目: {e(m)}</li>" for n, m in r["errors"])
    mp = "、".join(f"{e(k)} ← 「{e(v)}」" for k, v in r["mapping"].items())
    return f"""<p class="small"><a href="/reactions/import">選び直す</a></p><h1>取り込む前の確認</h1>
<div class="card"><h2>読み取った内容</h2><p class="small">列の対応: {mp}{"（使わない列: " + e("、".join(r["unknown"])) + "）" if r["unknown"] else ""}</p>
<div class="tablewrap"><table><tr><th>タイトル</th><th>公開日</th><th class="num">スキ</th><th class="num">コメント</th><th class="num">購入</th><th>メモ</th></tr>{rows}</table></div>
{"<p class='small muted'>（先頭の200行だけ表示）</p>" if len(r["rows"]) > 200 else ""}
{"<div class='card alert'><h2>読めなかった行</h2><ul>" + errs + "</ul></div>" if errs else ""}
<form method="post" action="/reactions/import/run"><input type="hidden" name="recorded" value="{e(recorded)}">
<textarea name="csv" hidden>{e(text)}</textarea>
<div class="btnrow"><button class="btn primary"{" disabled" if not r["rows"] else ""}>{len(r["rows"])} 件を記録する</button>
<a class="btn" href="/reactions">やめる</a></div></form></div>"""


def route_get(path: str, qs: dict):
    if path == "/reactions/import":
        return page_upload(), "CSV から取り込む", "reactions", ""
    return None


def back(path: str) -> str:
    return "/reactions/import"


def post(h, path: str, form) -> bool:
    if path == "/reactions/import/preview":
        ups = [data for _, _, data in form.files if data]
        if not ups:
            raise ValueError("ファイルを選んでください。")
        text = RC.decode(ups[0])
        h._page("取り込む前の確認", page_preview(text, form.get("recorded")), "reactions")
        return True
    if path == "/reactions/import/run":
        r = RC.parse(form.get("csv", strip=False), form.get("recorded"))
        added, skipped = RC.import_rows(r["rows"])
        h._redirect_to("/reactions", f"{added} 件を記録しました" + (f"（同じ記録 {skipped} 件は飛ばしました）" if skipped else ""))
        return True
    return False
