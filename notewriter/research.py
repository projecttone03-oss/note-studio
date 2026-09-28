"""リサーチ機能（トレンド調査・深掘り調査）の中核。画面（web.py）と CLI（__main__.py）はここを使う。

流れ: prepare()（送る全文・目安額・警告・確認トークンを作る）→ 人が確認 → start_job() / run_job()
      → 担当（Claude / Perplexity）で実行 → 記事ごとの「リサーチ資料」に Markdown で保存。

保存先（すべて作業フォルダ store.vault() 配下）:
- research/<記事名>/M0001.md   リサーチ資料（記事なしのトレンド調査は research/_trend/）
- research/jobs/J0001.json     ジョブの状態
- research/usage.json          使用記録（金額・トークン・検索回数）
- research/.claude-cwd/        Claude 実行時の作業ディレクトリ（空のまま）

守っていること:
- 外に送るのは人が入力した文章＋固定の指示テンプレートだけ（かけら・下書き・ネタ帳は読まない）。
- 有料（Perplexity）は確認トークンの一致と月の上限チェックを通ったときだけ実行する。
- Claude が利用上限に達しても Perplexity に自動で切り替えない（job.status="limit" で止めて聞く）。
- 同時に走らせるリサーチは1件まで（プロセスをまたいでファイルロックで確認）。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import compliance, perplexity, secrets, store
from .claude_runner import ClaudeLimitError, ClaudeRunError, run_claude
from .kakera import _alloc_id, _locked, _num

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

PROVIDER_KEYS = ("claude", "pplx_standard", "pplx_deep")
KINDS = {"trend": "トレンド調査（何を書くか）", "deep": "深掘り調査（材料集め）"}
OUTBOUND_CATEGORIES = ("属性", "属性の集中", "伏せ字")
CLAUDE_TOOLS = ("WebSearch", "WebFetch")
IMPORT_KIND = "import"
IMPORT_MAX_BYTES = 2 * 1024 * 1024
TREND_FOLDER = "_trend"
NO_ARTICLE_FOLDER = "_no_article"
NOTICE = "> この資料は書き方・背景の参考です。体験談の本文に新しい事実として混ぜないでください。"
PPLX_SYSTEM = "日本語で、Markdown 形式で回答してください。出典URLは実在を確認したものだけを書いてください。"

# テスト用: Perplexity の transport を差し替える（None なら urllib で本物を呼ぶ）
TRANSPORT = None


class BudgetExceeded(Exception):
    """月の上限金額を超える（または達している）ため、有料のリサーチを実行しない。"""


class ConfirmMismatch(ValueError):
    """確認画面で見せた内容と、実行しようとした内容が違う。"""


# ---------------------------------------------------------------- 設定

def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config() -> dict:
    """config/research.json を読み、作業フォルダの config/research.json でキー単位に上書きする
    （providers はプロバイダごとに項目単位で深くマージ）。"""
    cfg = store.read_json(store.CONFIG_DIR / "research.json", {}) or {}
    try:
        local = store.read_json(store.vault() / "config" / "research.json", {}) or {}
    except store.VaultError:
        local = {}
    for k, v in local.items():
        if k == "providers" and isinstance(v, dict):
            provs = dict(cfg.get("providers") or {})
            for pk, pv in v.items():
                provs[pk] = _merge(provs.get(pk) or {}, pv) if isinstance(pv, dict) else pv
            cfg["providers"] = provs
        else:
            cfg[k] = v
    cfg.setdefault("default_provider", "claude")
    cfg.setdefault("usd_jpy", 150)
    cfg.setdefault("monthly_budget_jpy", 1000)
    cfg.setdefault("estimate_safety_factor", 2.0)
    cfg.setdefault("freshness_days", 365)
    cfg.setdefault("providers", {})
    return cfg


def _provider_cfg(key: str, cfg: Optional[dict] = None) -> dict:
    if key not in PROVIDER_KEYS:
        raise ValueError(f"担当「{key}」は選べません（{'／'.join(PROVIDER_KEYS)}）。")
    cfg = cfg or load_config()
    p = (cfg.get("providers") or {}).get(key)
    if not isinstance(p, dict):
        raise ValueError(f"担当「{key}」の設定が config/research.json にありません。")
    return p


def _check_kind(kind: str) -> str:
    if kind not in KINDS:
        raise ValueError(f"リサーチの種類「{kind}」は選べません（{'／'.join(KINDS)}）。")
    return kind


def provider_label(key) -> str:
    try:
        return str(_provider_cfg(key).get("label") or key)
    except ValueError:
        return str(key or "")


def is_paid(key) -> bool:
    return _provider_cfg(key).get("type") == "perplexity"


# ---------------------------------------------------------------- 送る文章

def _prompt_path(kind: str) -> Path:
    return store.CONFIG_DIR / "research_prompts" / f"{kind}.md"


def build_prompt(kind, query) -> str:
    """固定テンプレートの {query} に人の入力だけを入れた、外に送る全文。"""
    _check_kind(kind)
    q = str(query or "").strip()
    if not q:
        raise ValueError("調べたい内容を入力してください。")
    tpl = _prompt_path(kind).read_text(encoding="utf-8")
    # 人の入力に {today} 等が含まれていても置き換わらないよう、{query} を最後に入れる
    tpl = tpl.replace("{today}", date.today().isoformat())
    return tpl.replace("{query}", q)


def outbound_warnings(text) -> List[dict]:
    """送る文章に、ぼかすべき属性・伏せ字リストの語が含まれていないか（止めるかは人が決める）。"""
    return [f.to_dict() for f in compliance.check_text(str(text or ""), name="送信内容")
            if f.category in OUTBOUND_CATEGORIES]


# ---------------------------------------------------------------- お金

def _jpy(usd: float, cfg: dict) -> float:
    return round(float(usd or 0) * float(cfg.get("usd_jpy") or 0), 2)


def _fmt_range(v) -> str:
    if isinstance(v, (list, tuple)) and v:
        a, b = v[0], v[-1]
        return f"{a:,}" if a == b else f"{a:,}〜{b:,}"
    return f"{v:,}" if isinstance(v, (int, float)) else str(v)


def estimate(provider_key, kind) -> dict:
    cfg = load_config()
    _check_kind(kind)
    p = _provider_cfg(provider_key, cfg)
    if p.get("type") != "perplexity":
        return {"paid": False, "usd_low": 0.0, "usd_high": 0.0, "jpy_low": 0, "jpy_high": 0,
                "basis": "Claude Code（Max 契約のサブスク認証）で実行するため追加料金はかかりません"
                         "（Claude の利用上限にはカウントされます）。",
                "pricing_note": ""}
    low, high = perplexity.estimate_cost(p, kind)
    rate = float(cfg.get("usd_jpy") or 0)
    est = (p.get("estimate") or {}).get(kind) or {}
    names = {"input_tokens": "入力トークン", "output_tokens": "出力トークン", "citation_tokens": "引用トークン",
             "reasoning_tokens": "推論トークン", "search_queries": "検索回数"}
    parts = [f"{names[k]} {_fmt_range(est[k])}" for k in names if k in est]
    size = p.get("search_context_size") or ""
    basis = f"モデル {p.get('model', '')}"
    if parts:
        basis += "、" + "・".join(parts) + " を想定"
    if size:
        basis += f"、検索の深さ {size}"
    basis += f"。1ドル={rate:g}円で換算（目安。実際のカード請求額とは異なる）。"
    notes = [str(cfg.get("_note") or ""), str((p.get("pricing") or {}).get("_note") or "")]
    return {"paid": True, "usd_low": round(low, 4), "usd_high": round(high, 4),
            "jpy_low": int(round(low * rate)), "jpy_high": int(math.ceil(high * rate)),
            "basis": basis, "pricing_note": " ".join(n for n in notes if n)}


def _usage_path() -> Path:
    return store.vault() / "research" / "usage.json"


def _read_usage() -> List[dict]:
    data = store.read_json(_usage_path(), []) or []
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []


def _this_month() -> str:
    return datetime.now().strftime("%Y-%m")


def month_usage(month: Optional[str] = None) -> dict:
    """月別の使用記録と合計。total_jpy は金額が分かっている分の合計。
    金額不明（通信切れ・5xx など課金されたか分からない）の実行は unknown_count と、
    その目安の上限 unknown_estimated_jpy を別に出し、remaining_jpy はそれも差し引いた控えめな値にする。"""
    month = month or _this_month()
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise ValueError(f"月は YYYY-MM の形で指定してください（{month}）。")
    cfg = load_config()
    runs = [r for r in _read_usage() if r.get("month") == month]
    total_usd = sum(float(r.get("cost_usd") or 0) for r in runs if not r.get("cost_unknown"))
    total_jpy = sum(float(r.get("cost_jpy") or 0) for r in runs if not r.get("cost_unknown"))
    unknown = [r for r in runs if r.get("cost_unknown")]
    unknown_jpy = sum(float(r.get("estimated_jpy_high") or 0) for r in unknown)
    budget = float(cfg.get("monthly_budget_jpy") or 0)
    return {"month": month, "total_jpy": round(total_jpy, 2), "total_usd": round(total_usd, 6),
            "budget_jpy": budget, "remaining_jpy": round(budget - total_jpy - unknown_jpy, 2),
            "unknown_count": len(unknown), "unknown_estimated_jpy": round(unknown_jpy, 2),
            "runs": sorted(runs, key=lambda r: str(r.get("at", "")), reverse=True), "count": len(runs)}


def budget_check(provider_key, kind) -> Tuple[bool, str]:
    if not is_paid(provider_key):
        return True, ""
    cfg = load_config()
    mu = month_usage()
    est = estimate(provider_key, kind)
    factor = float(cfg.get("estimate_safety_factor") or 1.0)
    budget = float(cfg.get("monthly_budget_jpy") or 0)
    used = mu["total_jpy"] + mu["unknown_estimated_jpy"]
    need = est["jpy_high"] * factor
    unknown_note = (f"（金額不明の実行 {mu['unknown_count']} 件を目安 {mu['unknown_estimated_jpy']:,.0f}円 として含む）"
                    if mu["unknown_count"] else "")
    if budget <= 0:
        return False, "月の上限金額が 0 円に設定されているため、Perplexity は実行しません（設定で変更できます）。"
    if used >= budget:
        return False, (f"今月の Perplexity の利用額が上限 {budget:,.0f}円 に達しています"
                       f"（今月の合計 {used:,.0f}円{unknown_note}）。来月まで実行を止めます。")
    if used + need > budget:
        return False, (f"今月の合計 {used:,.0f}円{unknown_note} ＋ 今回の目安の上限 {est['jpy_high']:,}円×{factor:g}"
                       f" が月の上限 {budget:,.0f}円 を超えるため実行しません。")
    return True, ""


def _record_usage(rec: dict) -> dict:
    with _locked():
        data = _read_usage()
        rid = _alloc_id("U", max([_num(str(r.get("id", ""))) for r in data] + [0]))
        now = datetime.now().replace(microsecond=0)
        full = {"id": rid, "at": now.isoformat(), "month": now.strftime("%Y-%m")}
        full.update(rec)
        data.append(full)
        store.write_json(_usage_path(), data)
    return full


# ---------------------------------------------------------------- 確認と実行

def _confirm_token(kind: str, provider_key: str, article: str, prompt: str) -> str:
    raw = json.dumps([kind, provider_key, article, prompt], ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _norm_article(article) -> str:
    return re.sub(r"[\r\n]+", " ", str(article or "")).strip()


def prepare(kind, provider_key, query, article="") -> dict:
    _check_kind(kind)
    _provider_cfg(provider_key)
    article = _norm_article(article)
    prompt = build_prompt(kind, query)
    ok, msg = budget_check(provider_key, kind)
    return {"kind": kind, "provider": provider_key, "provider_label": provider_label(provider_key),
            "article": article, "query": str(query).strip(), "prompt": prompt,
            "estimate": estimate(provider_key, kind), "warnings": outbound_warnings(str(query)),
            "budget_ok": ok, "budget_message": msg,
            "confirm_token": _confirm_token(kind, provider_key, article, prompt), "month": month_usage()}


_state_lock = threading.Lock()
_active: set = set()


def _research_dir() -> Path:
    return store.vault() / "research"


def _jobs_dir() -> Path:
    return _research_dir() / "jobs"


def _run_lock_path() -> Path:
    d = _research_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / ".run-lock"


def _try_run_lock() -> Optional[int]:
    """実行中ロックを取る。取れなければ None（別のリサーチが走っている）。"""
    fd = os.open(str(_run_lock_path()), os.O_RDWR | os.O_CREAT, 0o600)
    if fcntl is None:  # pragma: no cover
        return fd
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def _release(fd: Optional[int]) -> None:
    if fd is None:
        return
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _job_path(job_id: str) -> Path:
    return _jobs_dir() / f"{job_id}.json"


def _save_job(job: dict) -> None:
    store.write_json(_job_path(job["id"]), job)


def _begin(kind, provider_key, query, article, confirm_token) -> Tuple[dict, str, int]:
    """検証してジョブを作る。返り値 (job, prompt, lock_fd)。失敗時は例外（ロックは解放済み）。"""
    _check_kind(kind)
    _provider_cfg(provider_key)
    article = _norm_article(article)
    prompt = build_prompt(kind, query)
    if not confirm_token or confirm_token != _confirm_token(kind, provider_key, article, prompt):
        raise ConfirmMismatch("確認した内容と実行しようとした内容が一致しません。もう一度確認画面からやり直してください。")
    with _state_lock:
        if _active:
            raise ValueError("実行中のリサーチがあります。終わってから実行してください。")
        fd = _try_run_lock()
        if fd is None:
            raise ValueError("実行中のリサーチがあります。終わってから実行してください。")
        try:
            paid = is_paid(provider_key)
            if paid:
                ok, msg = budget_check(provider_key, kind)
                if not ok:
                    raise BudgetExceeded(msg)
                if not secrets.has_api_key():
                    raise ValueError("Perplexity の APIキーが設定されていません。設定から登録してください。")
            with _locked():
                existing = [_num(p.stem) for p in _jobs_dir().glob("J*.json")] if _jobs_dir().is_dir() else []
                jid = _alloc_id("J", max(existing + [0]))
                job = {"id": jid, "status": "running", "kind": kind, "provider": provider_key,
                       "provider_label": provider_label(provider_key), "article": article,
                       "query": str(query).strip(), "created": store.now(), "finished": "",
                       "material_id": "", "error": "", "limit_resets_at": "", "estimate_jpy_for_pplx": 0,
                       "model": "", "cost_jpy": 0.0}
                _save_job(job)
            _active.add(jid)
        except BaseException:
            _release(fd)
            raise
    return job, prompt, fd


def _finish(job: dict, fd: int) -> dict:
    job["finished"] = store.now()
    try:
        _save_job(job)
    finally:
        with _state_lock:
            _active.discard(job["id"])
            _release(fd)
    return job


def _execute(job: dict, prompt: str, fd: int) -> dict:
    kind, pk = job["kind"], job["provider"]
    try:
        cfg = load_config()
        p = _provider_cfg(pk, cfg)
        if p.get("type") == "claude":
            _execute_claude(job, prompt, p, cfg)
        else:
            _execute_pplx(job, prompt, p, cfg)
    except ClaudeLimitError as e:
        job["status"] = "limit"
        job["error"] = secrets.redact(str(e)) + " Perplexity には自動で切り替えていません。"
        job["limit_resets_at"] = getattr(e, "resets_at", "") or ""
        try:
            job["estimate_jpy_for_pplx"] = estimate("pplx_standard", kind)["jpy_high"]
        except Exception:
            job["estimate_jpy_for_pplx"] = 0
        _safe_record({"provider": pk, "model": job.get("model", ""), "kind": kind, "article": job["article"],
                      "status": "limit", "cost_usd": 0.0, "cost_jpy": 0.0, "cost_source": "free",
                      "cost_unknown": False, "estimated_jpy_high": 0, "usage": {}, "job_id": job["id"]})
    except (ClaudeRunError, perplexity.PplxError, BudgetExceeded, ValueError, store.VaultError, OSError) as e:
        job["status"] = "error"
        job["error"] = secrets.redact(str(e))
    except Exception as e:  # 想定外でもジョブは必ず終わらせる
        job["status"] = "error"
        job["error"] = secrets.redact(f"予期しないエラー（{type(e).__name__}）: {e}")
    return _finish(job, fd)


def _safe_record(rec: dict) -> None:
    try:
        _record_usage(rec)
    except Exception:
        pass


def _execute_claude(job: dict, prompt: str, p: dict, cfg: dict) -> None:
    kind = job["kind"]
    turns = p.get("max_turns")
    max_turns = int(turns.get(kind, 20) if isinstance(turns, dict) else (turns or 20))
    cwd = _research_dir() / ".claude-cwd"
    try:
        res = run_claude(prompt, CLAUDE_TOOLS, max_turns=max_turns, timeout_sec=int(p.get("timeout_sec") or 900),
                         cwd=cwd, model=str(p.get("model") or ""), command=str(p.get("command") or "claude"),
                         disallowed_tools=tuple(p.get("disallowed_tools") or ()),
                         limit_patterns=tuple(p.get("limit_patterns") or ()))
    except ClaudeLimitError:
        raise
    except ClaudeRunError:
        _safe_record({"provider": job["provider"], "model": str(p.get("model") or ""), "kind": kind,
                      "article": job["article"], "status": "error", "cost_usd": 0.0, "cost_jpy": 0.0,
                      "cost_source": "free", "cost_unknown": False, "estimated_jpy_high": 0, "usage": {},
                      "job_id": job["id"]})
        raise
    model = res.model or str(p.get("model") or "") or "（既定のモデル）"
    job["model"] = model
    if not res.text.strip():
        raise ClaudeRunError("Claude から空の結果が返りました。")
    mat = save_material(job["article"], kind, job["provider"], model, res.text, extract_urls(res.text),
                        query=job["query"], cost_usd=0.0, origin="auto")
    job["material_id"] = mat["id"]
    job["status"] = "done"
    _safe_record({"provider": job["provider"], "model": model, "kind": kind, "article": job["article"],
                  "status": "done", "cost_usd": 0.0, "cost_jpy": 0.0, "cost_source": "free",
                  "cost_unknown": False, "estimated_jpy_high": 0, "usage": res.raw_usage,
                  "web_search_requests": res.web_search_requests, "web_fetch_requests": res.web_fetch_requests,
                  "duration_ms": res.duration_ms, "job_id": job["id"], "material_id": mat["id"]})


def _execute_pplx(job: dict, prompt: str, p: dict, cfg: dict) -> None:
    kind, pk = job["kind"], job["provider"]
    ok, msg = budget_check(pk, kind)  # 実行直前にもう一度
    if not ok:
        raise BudgetExceeded(msg)
    est = estimate(pk, kind)
    model = str(p.get("model") or "")
    job["model"] = model
    base = {"provider": pk, "model": model, "kind": kind, "article": job["article"], "job_id": job["id"],
            "estimated_jpy_high": est["jpy_high"]}
    try:
        res = perplexity.call(prompt, p, secrets.get_api_key(), system=PPLX_SYSTEM, transport=TRANSPORT)
    except perplexity.PplxError as e:
        rec = dict(base, status="error", error_status=e.status, usage={})
        if e.maybe_charged:
            rec.update(cost_usd=None, cost_jpy=None, cost_source="unknown", cost_unknown=True)
        else:
            rec.update(cost_usd=0.0, cost_jpy=0.0, cost_source="none", cost_unknown=False)
        _safe_record(rec)
        raise
    cost_jpy = _jpy(res.cost_usd, cfg)
    job["model"] = res.model or model
    job["cost_jpy"] = cost_jpy
    body = res.text if res.text.strip() else "（Perplexity から本文が返りませんでした）"
    try:
        mat = save_material(job["article"], kind, pk, job["model"], body, res.sources, query=job["query"],
                            cost_usd=res.cost_usd, origin="auto")
    finally:
        _safe_record(dict(base, model=job["model"], status="done", cost_usd=res.cost_usd, cost_jpy=cost_jpy,
                          cost_source=res.cost_source, cost_unknown=False, usage=res.usage,
                          web_search_requests=int(res.usage.get("num_search_queries") or 0)))
    job["material_id"] = mat["id"]
    job["status"] = "done"


def start_job(kind, provider_key, query, article, confirm_token) -> str:
    job, prompt, fd = _begin(kind, provider_key, query, article, confirm_token)
    t = threading.Thread(target=_execute, args=(job, prompt, fd), name=f"research-{job['id']}", daemon=True)
    try:
        t.start()
    except BaseException as e:
        job["status"] = "error"
        job["error"] = f"実行を開始できませんでした（{type(e).__name__}）。"
        _finish(job, fd)
        raise
    return job["id"]


def run_job(kind, provider_key, query, article, confirm_token) -> dict:
    job, prompt, fd = _begin(kind, provider_key, query, article, confirm_token)
    return _execute(job, prompt, fd)


def _check_job_id(job_id) -> str:
    job_id = str(job_id or "").strip()
    if not re.fullmatch(r"J\d+", job_id):
        raise KeyError(job_id)
    return job_id


def _lock_is_free() -> bool:
    fd = _try_run_lock()
    if fd is None:
        return False
    _release(fd)
    return True


def get_job(job_id) -> dict:
    job_id = _check_job_id(job_id)
    job = store.read_json(_job_path(job_id))
    if not isinstance(job, dict):
        raise KeyError(job_id)
    if job.get("status") == "running":
        with _state_lock:
            orphan = job_id not in _active and _lock_is_free()
        if orphan:
            job["status"] = "error"
            job["error"] = "中断されました（実行中にアプリが終了した可能性があります）。"
            job["finished"] = job.get("finished") or store.now()
            try:
                _save_job(job)
            except (OSError, store.VaultError):
                pass
    return job


def list_jobs(limit=20) -> List[dict]:
    d = _jobs_dir()
    if not d.is_dir():
        return []
    ids = sorted((p.stem for p in d.glob("J*.json") if re.fullmatch(r"J\d+", p.stem)), key=_num, reverse=True)
    out = []
    for jid in ids[:max(0, int(limit))]:
        try:
            out.append(get_job(jid))
        except KeyError:
            continue
    return out


# ---------------------------------------------------------------- リサーチ資料

_MD_LINK_RX = re.compile(r"\[([^\]\n]{1,300})\]\((https?://[^\s)]+)\)")
_URL_RX = re.compile(r"https?://[^\s<>\"'`（）「」『』【】、。，]+")


def _clean_url(u: str) -> str:
    u = u.rstrip(".,;:!?*_>")
    while u.endswith(")") and u.count("(") < u.count(")"):
        u = u[:-1]
    while u.endswith("]") and u.count("[") < u.count("]"):
        u = u[:-1]
    return u


def extract_urls(text) -> List[dict]:
    """本文中の URL を出典として抜き出す（重複除去、Markdown リンクの文字をタイトルにする）。"""
    text = str(text or "")
    out: List[dict] = []
    seen: Dict[str, dict] = {}
    for m in _MD_LINK_RX.finditer(text):
        url = _clean_url(m.group(2))
        if url not in seen:
            item = {"title": m.group(1).strip(), "url": url, "date": ""}
            seen[url] = item
            out.append(item)
    for m in _URL_RX.finditer(text):
        url = _clean_url(m.group(0))
        if len(url) > 10 and url not in seen:
            item = {"title": "", "url": url, "date": ""}
            seen[url] = item
            out.append(item)
    return out


def _norm_sources(sources) -> List[dict]:
    out: List[dict] = []
    seen = set()
    for s in sources or []:
        if isinstance(s, str):
            s = {"url": s}
        if not isinstance(s, dict):
            continue
        url = re.sub(r"\s+", "", str(s.get("url") or ""))
        if not url or url in seen:
            continue
        seen.add(url)
        out.append({"title": re.sub(r"\s+", " ", str(s.get("title") or "")).strip(), "url": url,
                    "date": str(s.get("date") or "").strip()})
    return out


def _safe_folder(article: str, kind: str) -> str:
    name = _norm_article(article)
    if not name:
        return TREND_FOLDER if kind == "trend" else NO_ARTICLE_FOLDER
    name = re.sub(r"[\x00-\x1f\x7f/\\:*?\"<>|]", "_", name)
    name = name.replace("..", "_").strip(" .")
    if not name or name.startswith("_") or name.lower() in ("jobs",):
        name = "記事_" + name
    return name[:80]


def _to_date(v) -> str:
    if v is None or v == "":
        return date.today().isoformat()
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    s = str(v).strip()[:10]
    try:
        return datetime.strptime(s, "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise ValueError(f"調べた日は YYYY-MM-DD の形で指定してください（{v}）。") from None


def _title_of(body: str, fallback: str) -> str:
    for line in str(body or "").splitlines():
        m = re.match(r"#{1,3}\s+(.+)", line.strip())
        if m:
            return m.group(1).strip()[:80]
    return (re.sub(r"\s+", " ", fallback or "").strip()[:60]) or "リサーチ資料"


def _all_material_paths() -> List[Path]:
    d = _research_dir()
    if not d.is_dir():
        return []
    return [p for p in d.glob("*/M*.md") if re.fullmatch(r"M\d+", p.stem) and p.parent.name != "jobs"]


def _provider_display(provider: str) -> str:
    return provider_label(provider) if provider in PROVIDER_KEYS else str(provider or "")


def save_material(article, kind, provider_key_or_label, model, body_md, sources, query="", researched_at=None,
                  cost_usd=0.0, origin="auto") -> dict:
    article = _norm_article(article)
    kind = str(kind or "")
    researched = _to_date(researched_at)
    srcs = _norm_sources(sources)
    provider = str(provider_key_or_label or "")
    label = _provider_display(provider)
    cfg = load_config()
    cost_usd = float(cost_usd or 0)
    kind_label = KINDS.get(kind, "手動取り込み" if kind == IMPORT_KIND else kind)
    header = [NOTICE,
              f"> 調べた日: {researched} / 担当: {label} / モデル: {model or '（不明）'}",
              f"> 種類: {kind_label}"]
    parts = ["\n".join(header), str(body_md or "").strip()]
    if srcs:
        lines = ["## 出典（自動抽出）", ""]
        for s in srcs:
            t = s["title"] or s["url"]
            lines.append(f"- [{t}]({s['url']})" + (f"（{s['date']}）" if s["date"] else ""))
        parts.append("\n".join(lines))
    body = "\n\n".join(p for p in parts if p) + "\n"
    with _locked():
        mid = _alloc_id("M", max([_num(p.stem) for p in _all_material_paths()] + [0]))
        path = _research_dir() / _safe_folder(article, kind) / f"{mid}.md"
        meta = {"id": mid, "article": article, "kind": kind, "provider": provider, "provider_label": label,
                "model": model or "", "researched_at": researched, "query": query or "",
                "title": _title_of(body_md, query), "cost_usd": f"{cost_usd:.6f}",
                "cost_jpy": f"{_jpy(cost_usd, cfg):.2f}", "origin": origin, "created": store.now(),
                # 出典は1行の JSON で持つ（store の list 形式はカンマで分割されて URL が壊れるため）
                "sources": json.dumps(srcs, ensure_ascii=False)}
        store.atomic_write(path, store.dump_md(meta, body))
    return get_material(mid)


def _read_material(path: Path, with_body: bool = False) -> dict:
    meta, body = store.parse_md(path.read_text(encoding="utf-8"))
    try:
        srcs = _norm_sources(json.loads(str(meta.get("sources") or "[]")))
    except ValueError:
        srcs = extract_urls(body)
    researched = str(meta.get("researched_at") or "")
    try:
        age = (date.today() - datetime.strptime(researched[:10], "%Y-%m-%d").date()).days
    except ValueError:
        age = None
    fresh = int(load_config().get("freshness_days") or 365)
    try:
        cost_jpy = float(meta.get("cost_jpy") or 0)
    except ValueError:
        cost_jpy = 0.0
    item = {"id": str(meta.get("id") or path.stem), "article": str(meta.get("article") or ""),
            "kind": str(meta.get("kind") or ""), "provider": str(meta.get("provider") or ""),
            "provider_label": str(meta.get("provider_label") or meta.get("provider") or ""),
            "model": str(meta.get("model") or ""), "researched_at": researched, "age_days": age,
            "stale": age is not None and age >= fresh, "sources": srcs, "query": str(meta.get("query") or ""),
            "cost_jpy": cost_jpy, "origin": str(meta.get("origin") or ""),
            "title": str(meta.get("title") or path.stem), "path": str(path)}
    if with_body:
        item["body"] = body.rstrip("\n")
    return item


def list_materials(article=None) -> List[dict]:
    items = [_read_material(p) for p in _all_material_paths()]
    if article is not None:
        a = _norm_article(article)
        items = [m for m in items if m["article"] == a]
    items.sort(key=lambda m: (m["researched_at"], _num(m["id"])), reverse=True)
    return items


def _material_path(mid) -> Path:
    mid = str(mid or "").strip()
    if not re.fullmatch(r"M\d+", mid):
        raise KeyError(mid)
    for p in _all_material_paths():
        if p.stem == mid:
            return p
    raise KeyError(mid)


def get_material(mid) -> dict:
    return _read_material(_material_path(mid), with_body=True)


def delete_material(mid) -> None:
    with _locked():
        _material_path(mid).unlink()


def stale_materials() -> List[dict]:
    return [m for m in list_materials() if m["stale"]]


def import_material(filename, text, article="", researched_at=None, provider="手動取り込み") -> dict:
    """手で調べた Markdown（Perplexity Pro の書き出し等）をリサーチ資料として取り込む。"""
    name = Path(str(filename or "")).name
    if not name.lower().endswith(".md"):
        raise ValueError("取り込めるのは .md（Markdown）ファイルだけです。")
    if isinstance(text, bytes):
        if len(text) > IMPORT_MAX_BYTES:
            raise ValueError("ファイルが大きすぎます（1ファイル 2MB まで）。")
        try:
            text = text.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("文字コードが UTF-8 ではないため取り込めません。") from None
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    if len(text.encode("utf-8")) > IMPORT_MAX_BYTES:
        raise ValueError("ファイルが大きすぎます（1ファイル 2MB まで）。")
    if not text.strip():
        raise ValueError("ファイルが空です。")
    title_hint = name[:-3]
    return save_material(article, IMPORT_KIND, provider or "手動取り込み", "", text, extract_urls(text),
                         query=title_hint, researched_at=researched_at, cost_usd=0.0, origin="import")
