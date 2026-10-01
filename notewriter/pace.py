"""執筆ペースの表示（SPEC.md 機能14）。責めない形で、続けてきたことだけを見せる。

- 週ごとの「かけら・ネタ・公開」の数（直近8週）。連続記録・ノルマ・赤い警告は出さない。
- 言葉は config/pace.json で変えられる。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Dict, List

from . import kakera as K
from . import store

DEFAULTS = {
    "weeks": 8,
    "messages": {
        "active": "今週は {kakera} 個のかけらを書きました。書いた分だけ、記事の材料が増えています。",
        "quiet": "今週はお休み中です。書きたくなったら、ネタ帳に一言だけでも十分です。",
        "total": "これまでに かけら {kakera_total} 個・ネタ {neta_total} 個・公開 {published_total} 本。"
    },
}


def config() -> dict:
    cfg = dict(DEFAULTS)
    cfg.update(store.read_json(store.CONFIG_DIR / "pace.json", {}) or {})
    try:
        cfg.update(store.read_json(store.vault() / "config" / "pace.json", {}) or {})
    except store.VaultError:
        pass
    return cfg


def _day(s: str):
    try:
        return datetime.strptime(str(s or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def weekly(today=None) -> List[Dict[str, object]]:
    from . import published
    today = today or date.today()
    n = int(config().get("weeks") or 8)
    start0 = today - timedelta(days=today.weekday())  # 今週の月曜
    weeks = [{"start": start0 - timedelta(weeks=i), "kakera": 0, "neta": 0, "published": 0} for i in range(n)]

    def put(d, key):
        if d is None:
            return
        for w in weeks:
            if w["start"] <= d < w["start"] + timedelta(days=7):
                w[key] += 1
                return

    for k in K.list_kakera():
        put(_day(k["created"]), "kakera")
    for x in K.list_neta():
        put(_day(x["created"]), "neta")
    for p in published.list_published():
        put(_day(p["published"]), "published")
    return weeks


def summary(today=None) -> dict:
    from . import published
    cfg = config()
    weeks = weekly(today)
    this = weeks[0]
    msg = cfg["messages"]
    head = (msg["active"] if this["kakera"] or this["neta"] or this["published"] else msg["quiet"])
    head = head.replace("{kakera}", str(this["kakera"])).replace("{neta}", str(this["neta"])).replace("{published}", str(this["published"]))
    totals = {"kakera_total": len(K.list_kakera()), "neta_total": len(K.list_neta()),
              "published_total": len(published.list_published())}
    total = msg["total"]
    for k, v in totals.items():
        total = total.replace("{" + k + "}", str(v))
    return {"weeks": weeks, "headline": head, "total": total, **totals}
