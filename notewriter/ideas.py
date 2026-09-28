"""ネタ出し機能の中核。画面（web.py）と CLI（__main__.py）はここを使う。

流れ: prepare()（材料を入れた送信全文・件数・確認トークン）→ 人が確認 → start_job() / run_job()
      → Claude Code（ツールなし・Web検索なし）で実行 → 候補を JSON で受け取り、ideas/I0001.json に保存
      → 人が候補を選ぶ（未検討／調査に回した／保留／却下）→ 選んだ候補をトレンド調査の入力欄へ。

保存先（すべて作業フォルダ store.vault() 配下。GitHub には上げない）:
- profile/skills.md        得意・経験リスト（実データ）
- ideas/I0001.json         1回のネタ出し（候補と状態。材料の本文は保存せず件数だけ）
- ideas/jobs/IJ0001.json   ジョブの状態
- ideas/.claude-cwd/       Claude 実行時の作業ディレクトリ（空のまま。リサーチ用とは別）

守っていること:
- 材料は 得意・経験リスト、ネタ帳（かけら化済みのメモは除く）、反応記録の要約（タイトルと数値）、
  今日の日付、過去の候補（却下したネタを含む）だけ。かけら（体験談の素材）は読まない。
- Claude はツールをすべて拒否（--tools "" ＋ PreToolUse フック ＋ init のツール一覧の検証）。材料は標準入力で渡す。
- 確認画面で見せた全文と、実行する全文が同じときだけ実行する（確認トークン）。
- 利用上限に達したら止まるだけ（Perplexity には切り替えない）。
- ネタ出し同士は同時に1件まで（プロセスをまたいでファイルロックで確認）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import unicodedata
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from . import reactions, secrets, store
from .claude_runner import ClaudeLimitError, ClaudeRunError, run_claude
from .kakera import _alloc_id, _locked, _num

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

STATUSES = ("未検討", "調査に回した", "保留", "却下")
TYPES = ("体験談", "リサーチ型")
UNKNOWN_TYPE = "不明"
HYPOTHESIS = "仮説（まだ需要を確かめていない）"
PROMPT_FILE = "ideas.md"
EMPTY = "（なし）"
DEFAULT_CONFIG = {
    "command": "claude", "model": "", "max_turns": 2, "timeout_sec": 300, "candidate_count": 10,
    "skills_max_chars": 4000, "neta_max_items": 30, "neta_max_chars": 200, "past_max_items": 60,
    "rejected_max_items": 100, "reactions_top": 10, "limit_patterns": [], "disallowed_tools": [],
}
# 1候補の各項目の長さの上限（おかしな応答で履歴が膨らまないように）
_LIMITS = {"idea": 200, "why_me": 600, "reader": 300, "item": 200, "items": 10}
MAX_CANDIDATES = 40
MAX_RAW = 50000


class ConfirmMismatch(ValueError):
    """確認画面で見せた内容と、実行しようとした内容が違う（材料が変わった等）。"""


# ---------------------------------------------------------------- 設定

def load_config() -> dict:
    """config/ideas.json を読み、作業フォルダの config/ideas.json でキー単位に上書きする。"""
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(store.read_json(store.CONFIG_DIR / "ideas.json", {}) or {})
    try:
        local = store.read_json(store.vault() / "config" / "ideas.json", {}) or {}
    except store.VaultError:
        local = {}
    cfg.update(local)
    return cfg


def _int(cfg: dict, key: str) -> int:
    try:
        return max(0, int(cfg.get(key) if cfg.get(key) is not None else DEFAULT_CONFIG[key]))
    except (TypeError, ValueError):
        return int(DEFAULT_CONFIG[key])


# ---------------------------------------------------------------- 得意・経験リスト

def _skills_path() -> Path:
    return store.vault() / "profile" / "skills.md"


def get_skills() -> str:
    try:
        return _skills_path().read_text(encoding="utf-8").rstrip("\n")
    except FileNotFoundError:
        return ""


def save_skills(text) -> None:
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    with _locked():
        store.atomic_write(_skills_path(), text + "\n" if text else "")


# ---------------------------------------------------------------- 候補の保存・状態

def _ideas_dir() -> Path:
    return store.vault() / "ideas"


def _session_path(sid: str) -> Path:
    return _ideas_dir() / f"{sid}.json"


def _check_sid(sid) -> str:
    sid = str(sid or "").strip()
    if not re.fullmatch(r"I\d+", sid):
        raise KeyError(sid)
    return sid


def _session_ids() -> List[str]:
    d = _ideas_dir()
    if not d.is_dir():
        return []
    return sorted((p.stem for p in d.glob("I*.json") if re.fullmatch(r"I\d+", p.stem)), key=_num)


def get_session(sid) -> dict:
    sid = _check_sid(sid)
    data = store.read_json(_session_path(sid))
    if not isinstance(data, dict):
        raise KeyError(sid)
    data.setdefault("candidates", [])
    return data


def list_sessions() -> List[dict]:
    """過去のネタ出し（新しい順）。"""
    out = []
    for sid in reversed(_session_ids()):
        try:
            out.append(get_session(sid))
        except KeyError:
            continue
    return out


def list_candidates(status: Optional[str] = None) -> List[dict]:
    """すべての候補（新しいネタ出し順）。各候補に session_id と created（ネタ出しの日時）を付ける。"""
    if status is not None and status not in STATUSES:
        raise ValueError(f"状態「{status}」は選べません（{'／'.join(STATUSES)}）。")
    out = []
    for s in list_sessions():
        for c in s.get("candidates") or []:
            if status is None or c.get("status") == status:
                item = dict(c)
                item["session_id"] = s.get("id", "")
                item["created"] = s.get("created", "")
                out.append(item)
    return out


def counts_by_status() -> Dict[str, int]:
    counts = {s: 0 for s in STATUSES}
    for c in list_candidates():
        counts[c.get("status")] = counts.get(c.get("status"), 0) + 1
    return counts


def _split_cid(cid) -> Tuple[str, str]:
    cid = str(cid or "").strip()
    m = re.fullmatch(r"(I\d+)-\d+", cid)
    if not m:
        raise KeyError(cid)
    return m[1], cid


def get_candidate(cid) -> dict:
    sid, cid = _split_cid(cid)
    for c in get_session(sid).get("candidates") or []:
        if c.get("cid") == cid:
            item = dict(c)
            item["session_id"] = sid
            return item
    raise KeyError(cid)


def set_status(cid, status) -> dict:
    status = str(status or "").strip()
    if status not in STATUSES:
        raise ValueError(f"状態「{status}」は選べません（{'／'.join(STATUSES)}）。")
    sid, cid = _split_cid(cid)
    with _locked():
        s = get_session(sid)
        for c in s["candidates"]:
            if c.get("cid") == cid:
                c["status"] = status
                c["status_updated"] = store.now()
                store.write_json(_session_path(sid), s)
                return dict(c, session_id=sid)
    raise KeyError(cid)


def set_statuses(cids: Sequence[str], status) -> List[dict]:
    """まとめて状態を変える（存在しない ID が1つでもあれば何も変えずに KeyError）。"""
    ids = []
    for c in cids or ():
        if c and c not in ids:
            ids.append(c)
    if not ids:
        raise ValueError("候補を1つ以上選んでください。")
    with _locked():
        for c in ids:
            get_candidate(c)
        return [set_status(c, status) for c in ids]


def norm_idea(text) -> str:
    """同じネタかどうかを比べるための正規化（全角半角・大文字小文字・空白・記号の違いを無視）。"""
    s = unicodedata.normalize("NFKC", str(text or "")).casefold()
    return re.sub(r"[\W_]+", "", s)


def rejected_ideas() -> List[str]:
    """却下したネタ（新しい順、重複なし）。"""
    out, seen = [], set()
    for c in list_candidates("却下"):
        key = norm_idea(c.get("idea"))
        if key and key not in seen:
            seen.add(key)
            out.append(str(c.get("idea") or ""))
    return out


# ---------------------------------------------------------------- 材料と送る全文

def _neta_items(cfg: dict) -> List[str]:
    """ネタ帳のメモ（新しい順・かけら化済みは除く）。かけら本体は読まない。"""
    from . import kakera
    n_max, c_max = _int(cfg, "neta_max_items"), _int(cfg, "neta_max_chars")
    items = [n for n in kakera.list_neta() if n.get("status") != "かけら化済み" and str(n.get("body") or "").strip()]
    items.sort(key=lambda n: (str(n.get("created") or ""), _num(str(n.get("id") or ""))), reverse=True)
    out = []
    for n in items[:n_max]:
        t = re.sub(r"\s+", " ", str(n.get("body") or "")).strip()
        out.append(t if len(t) <= c_max else t[:c_max] + "…")
    return out


def _past_items(cfg: dict) -> List[str]:
    out, seen = [], set()
    for c in list_candidates():
        if c.get("status") == "却下":
            continue
        key = norm_idea(c.get("idea"))
        if key and key not in seen:
            seen.add(key)
            out.append(str(c.get("idea") or ""))
        if len(out) >= _int(cfg, "past_max_items"):
            break
    return out


def _reaction_lines(cfg: dict) -> List[str]:
    return [f"「{r['title']}」 スキ {r['likes']}・コメント {r['comments']}・購入 {r['purchases']}"
            + (f"（公開 {r['published']}）" if r.get("published") else "")
            for r in reactions.top_reactions(_int(cfg, "reactions_top"))]


def _materials(cfg: dict) -> dict:
    skills = get_skills().strip()
    s_max = _int(cfg, "skills_max_chars")
    if len(skills) > s_max:
        skills = skills[:s_max] + "…（長いので途中まで）"
    rejected = rejected_ideas()[:_int(cfg, "rejected_max_items")]
    return {"skills": skills, "neta": _neta_items(cfg), "reactions": _reaction_lines(cfg),
            "past": _past_items(cfg), "rejected": rejected}


def _summary(m: dict) -> dict:
    return {"skills_chars": len(m["skills"]), "neta": len(m["neta"]), "reactions": len(m["reactions"]),
            "past": len(m["past"]), "rejected": len(m["rejected"])}


def materials_summary() -> dict:
    """材料の件数（本文は含まない）。skills_chars は得意・経験リストの文字数。"""
    return _summary(_materials(load_config()))


def summary_text(s: dict) -> str:
    return (f"ネタ帳 {s.get('neta', 0)}件・反応記録 {s.get('reactions', 0)}件・過去候補 {s.get('past', 0)}件・"
            f"却下 {s.get('rejected', 0)}件・得意経験リスト {s.get('skills_chars', 0)}字")


def _bullets(items: List[str]) -> str:
    return "\n".join("- " + x for x in items) if items else EMPTY


def _build(cfg: dict) -> Tuple[str, dict]:
    m = _materials(cfg)
    tpl = (store.CONFIG_DIR / "idea_prompts" / PROMPT_FILE).read_text(encoding="utf-8")
    values = {"today": date.today().isoformat(), "count": str(_int(cfg, "candidate_count") or 10),
              "skills": m["skills"] or "（まだ書かれていません）", "neta": _bullets(m["neta"]),
              "reactions": _bullets(m["reactions"]), "past": _bullets(m["past"]),
              "rejected": _bullets(m["rejected"])}
    # 1回の置き換えで入れる（人の書いた材料に {today} 等が含まれていても、二重に置き換わらない）
    prompt = re.sub(r"\{(today|count|skills|neta|reactions|past|rejected)\}", lambda x: values[x.group(1)], tpl)
    return prompt, _summary(m)


def build_prompt() -> str:
    """材料を入れた、Claude に渡す全文。"""
    return _build(load_config())[0]


def _token(prompt: str) -> str:
    return hashlib.sha256(("ideas\n" + prompt).encode("utf-8")).hexdigest()


def prepare() -> dict:
    prompt, summary = _build(load_config())
    return {"prompt": prompt, "chars": len(prompt), "summary": summary, "confirm_token": _token(prompt)}


# ---------------------------------------------------------------- 応答の読み取り

_FENCE = re.compile(r"```(?:json|JSON)?\s*\n(.*?)\n?```", re.S)


def _as_str(v, limit: int) -> str:
    s = re.sub(r"\s+", " ", "" if v is None else str(v)).strip()
    return s[:limit]


def _as_items(v) -> List[str]:
    if v is None:
        return []
    items = [v] if isinstance(v, str) else (list(v) if isinstance(v, (list, tuple)) else [v])
    out = []
    for x in items:
        if isinstance(x, (dict, list)):
            continue
        s = _as_str(x, _LIMITS["item"])
        if s and s not in out:
            out.append(s)
    return out[:_LIMITS["items"]]


def parse_candidates(text) -> List[dict]:
    """Claude の応答から候補を取り出す。読めなければ ValueError。"""
    text = str(text or "")
    blocks = [b for b in _FENCE.findall(text)]
    i, j = text.find("{"), text.rfind("}")
    if i != -1 and j > i:
        blocks.append(text[i:j + 1])
    data = None
    for b in blocks:
        try:
            d = json.loads(b)
        except ValueError:
            continue
        if isinstance(d, dict) and isinstance(d.get("candidates"), list):
            data = d
            break
        if isinstance(d, list):
            data = {"candidates": d}
            break
    if data is None:
        raise ValueError("Claude の応答から候補（JSON）を読み取れませんでした。")
    out = []
    for c in data["candidates"]:
        if not isinstance(c, dict):
            continue
        idea = _as_str(c.get("idea"), _LIMITS["idea"])
        if not idea:
            continue
        typ = _as_str(c.get("type"), 20)
        out.append({"idea": idea, "why_me": _as_str(c.get("why_me"), _LIMITS["why_me"]),
                    "skills": _as_items(c.get("skills")), "reader": _as_str(c.get("reader"), _LIMITS["reader"]),
                    "type": typ if typ in TYPES else UNKNOWN_TYPE, "check_points": _as_items(c.get("check_points"))})
        if len(out) >= MAX_CANDIDATES:
            break
    if not out:
        raise ValueError("Claude の応答に、使える候補がありませんでした。")
    return out


def _save_session(model: str, summary: dict, prompt_chars: int, candidates: List[dict], excluded: int,
                  excluded_dup: int = 0, raw: str = "", error: str = "") -> dict:
    with _locked():
        sid = _alloc_id("I", max([_num(x) for x in _session_ids()] + [0]))
        ts = store.now()
        cands = []
        for n, c in enumerate(candidates, 1):
            item = {"cid": f"{sid}-{n:02d}"}
            item.update(c)
            item.update({"status": "未検討", "status_updated": ts})
            cands.append(item)
        s = {"id": sid, "created": ts, "model": model, "materials_summary": summary, "prompt_chars": prompt_chars,
             "candidates": cands, "excluded_rejected": excluded, "excluded_duplicates": excluded_dup}
        if raw:
            s["raw"] = raw[:MAX_RAW]
        if error:
            s["error"] = error
        store.write_json(_session_path(sid), s)
    return s


# ---------------------------------------------------------------- ジョブ

_state_lock = threading.Lock()
_active: set = set()


def _jobs_dir() -> Path:
    return _ideas_dir() / "jobs"


def _job_path(jid: str) -> Path:
    return _jobs_dir() / f"{jid}.json"


def _run_lock_path() -> Path:
    d = _ideas_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / ".run-lock"


def _try_run_lock() -> Optional[int]:
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


def _save_job(job: dict) -> None:
    store.write_json(_job_path(job["id"]), job)


def _begin(confirm_token) -> Tuple[dict, str, dict, int]:
    prompt, summary = _build(load_config())
    if not confirm_token or confirm_token != _token(prompt):
        raise ConfirmMismatch("確認した内容と、いま送る内容が違います（材料が変わった可能性があります）。"
                              "もう一度確認画面からやり直してください。")
    busy = "実行中のネタ出しがあります。終わってから実行してください。"
    with _state_lock:
        if _active:
            raise ValueError(busy)
        fd = _try_run_lock()
        if fd is None:
            raise ValueError(busy)
        try:
            with _locked():
                existing = [_num(p.stem) for p in _jobs_dir().glob("IJ*.json")] if _jobs_dir().is_dir() else []
                jid = _alloc_id("IJ", max(existing + [0]))
                job = {"id": jid, "status": "running", "created": store.now(), "finished": "", "session_id": "",
                       "count": 0, "excluded_rejected": 0, "error": "", "limit_resets_at": "", "model": "",
                       "prompt_chars": len(prompt), "materials_summary": summary}
                _save_job(job)
            _active.add(jid)
        except BaseException:
            _release(fd)
            raise
    return job, prompt, summary, fd


def _finish(job: dict, fd: int) -> dict:
    job["finished"] = store.now()
    try:
        _save_job(job)
    finally:
        with _state_lock:
            _active.discard(job["id"])
            _release(fd)
    return job


def limit_message(resets_at: str = "") -> str:
    when = f"（{resets_at}ごろ解除）" if resets_at else "（解除の時刻は分かりませんでした）"
    return f"Claude の利用上限に達しました{when}。時間をおいてもう一度お試しください。"


def _execute(job: dict, prompt: str, summary: dict, fd: int) -> dict:
    try:
        cfg = load_config()
        res = run_claude(prompt, (), max_turns=_int(cfg, "max_turns") or 2,
                         timeout_sec=_int(cfg, "timeout_sec") or 300, cwd=_ideas_dir() / ".claude-cwd",
                         model=str(cfg.get("model") or ""), command=str(cfg.get("command") or "claude"),
                         disallowed_tools=tuple(cfg.get("disallowed_tools") or ()),
                         limit_patterns=tuple(cfg.get("limit_patterns") or ()))
        model = res.model or str(cfg.get("model") or "") or "（既定のモデル）"
        job["model"] = model
        if not res.text.strip():
            raise ClaudeRunError("Claude から空の結果が返りました。")
        try:
            cands = parse_candidates(res.text)
        except ValueError as ex:
            s = _save_session(model, summary, len(prompt), [], 0, raw=res.text, error=str(ex))
            job["session_id"] = s["id"]
            job["status"] = "error"
            job["error"] = str(ex) + " 返ってきた文章は履歴に残しました。もう一度試すときは「ネタを出す」からやり直してください。"
            return _finish(job, fd)
        rejected = {norm_idea(x) for x in rejected_ideas()}
        kept, seen, excluded, dup = [], set(), 0, 0
        for c in cands:
            key = norm_idea(c["idea"])
            if key in rejected:
                excluded += 1
                continue
            if key in seen:
                dup += 1
                continue
            seen.add(key)
            kept.append(c)
        s = _save_session(model, summary, len(prompt), kept, excluded, dup)
        job.update({"session_id": s["id"], "count": len(kept), "excluded_rejected": excluded, "status": "done"})
    except ClaudeLimitError as e:
        job["status"] = "limit"
        job["limit_resets_at"] = getattr(e, "resets_at", "") or ""
        job["error"] = limit_message(job["limit_resets_at"])
    except (ClaudeRunError, ValueError, store.VaultError, OSError) as e:
        job["status"] = "error"
        job["error"] = secrets.redact(str(e))
    except Exception as e:  # 想定外でもジョブは必ず終わらせる
        job["status"] = "error"
        job["error"] = secrets.redact(f"予期しないエラー（{type(e).__name__}）: {e}")
    return _finish(job, fd)


def start_job(confirm_token) -> str:
    job, prompt, summary, fd = _begin(confirm_token)
    t = threading.Thread(target=_execute, args=(job, prompt, summary, fd), name=f"ideas-{job['id']}", daemon=True)
    try:
        t.start()
    except BaseException as e:
        job["status"] = "error"
        job["error"] = f"実行を開始できませんでした（{type(e).__name__}）。"
        _finish(job, fd)
        raise
    return job["id"]


def run_job(confirm_token) -> dict:
    job, prompt, summary, fd = _begin(confirm_token)
    return _execute(job, prompt, summary, fd)


def _lock_is_free() -> bool:
    fd = _try_run_lock()
    if fd is None:
        return False
    _release(fd)
    return True


def get_job(jid) -> dict:
    jid = str(jid or "").strip()
    if not re.fullmatch(r"IJ\d+", jid):
        raise KeyError(jid)
    job = store.read_json(_job_path(jid))
    if not isinstance(job, dict):
        raise KeyError(jid)
    if job.get("status") == "running":
        with _state_lock:
            orphan = jid not in _active and _lock_is_free()
        if orphan:
            job["status"] = "error"
            job["error"] = "中断されました（実行中にアプリが終了した可能性があります）。"
            job["finished"] = job.get("finished") or store.now()
            try:
                _save_job(job)
            except (OSError, store.VaultError):
                pass
    return job


def running_job() -> Optional[dict]:
    """いま実行中のジョブ（なければ None）。"""
    d = _jobs_dir()
    if not d.is_dir():
        return None
    ids = sorted((p.stem for p in d.glob("IJ*.json") if re.fullmatch(r"IJ\d+", p.stem)), key=_num, reverse=True)
    for jid in ids[:5]:
        try:
            j = get_job(jid)
        except KeyError:
            continue
        if j.get("status") == "running":
            return j
    return None


# ---------------------------------------------------------------- トレンド調査へ

def research_query(cids: Sequence[str]) -> str:
    """選んだ候補から、トレンド調査の質問文を作る。使うのは「ネタ」と「確かめるべき点」だけ
    （なぜ私に向いているか・得意経験・ネタ帳の文は入れない。外に出る量を最小にするため）。"""
    ids = []
    for c in cids or ():
        if c and c not in ids:
            ids.append(c)
    if not ids:
        raise ValueError("候補を1つ以上選んでください。")
    lines = ["次のネタ候補について、今の需要を確かめたい。"]
    for n, cid in enumerate(ids, 1):
        c = get_candidate(cid)
        pts = "／".join(c.get("check_points") or [])
        lines.append(f"{n}. {c.get('idea', '')}" + (f"（確かめたい点: {pts}）" if pts else ""))
    return "\n".join(lines)
