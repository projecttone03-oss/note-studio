"""ツールなしの Claude を裏で1回呼ぶ「ジョブ」の共通部分。

文体ルール候補・参考書籍の分析・リサーチ型記事の下書き・SNS 投稿文などで使う。
下書き作成（writing.py）と同じ守り方をする:
- Claude はツールなし・MCPなしで起動し、指示と材料は標準入力で渡す（claude_runner。init のツール一覧を検証）。
- 起動の設定（command・model・disallowed_tools・limit_patterns）は config/writing.json と同じものを使う。
- 確認画面で見せた全文と同じ内容のときだけ実行する（確認トークン）。
- 同じ種類のジョブは同時に1件まで（プロセスをまたいでファイルロック）。

保存先: 作業フォルダの jobs/<種類>/<接頭辞>0001.json
"""
from __future__ import annotations

import hashlib
import os
import re
import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import secrets, store
from .claude_runner import ClaudeLimitError, ClaudeRunError, run_claude
from .kakera import _alloc_id, _locked, _num

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

STATUS = {"running": "実行中", "done": "完了", "error": "失敗", "limit": "利用上限", "conflict": "保留"}


class ConfirmMismatch(ValueError):
    """確認した内容と、いま送る内容が違う。"""


def token(*parts: str) -> str:
    return hashlib.sha256("\n\x00".join(str(p) for p in parts).encode("utf-8")).hexdigest()


def claude_config() -> dict:
    from . import writing
    return writing.load_config()


def limit_message(resets_at: str = "") -> str:
    when = f"（{resets_at}ごろ解除）" if resets_at else "（解除の時刻は分かりませんでした）"
    return f"Claude の利用上限に達しました{when}。時間をおいてもう一度お試しください。"


class JobKind:
    """ジョブの種類（kind: フォルダ名、prefix: ID の頭文字、busy: 実行中のときの文言）。"""

    def __init__(self, kind: str, prefix: str, busy: str):
        self.kind, self.prefix, self.busy = kind, prefix, busy
        self._lock = threading.Lock()
        self._active: set = set()

    # ---- 保存先
    def dir(self) -> Path:
        return store.vault() / "jobs" / self.kind

    def _path(self, jid: str) -> Path:
        return self.dir() / f"{jid}.json"

    def save(self, job: dict) -> None:
        store.write_json(self._path(job["id"]), job)

    # ---- ロック
    def _try_lock(self) -> Optional[int]:
        d = self.dir()
        d.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(d / ".run-lock"), os.O_RDWR | os.O_CREAT, 0o600)
        if fcntl is None:  # pragma: no cover
            return fd
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return None
        return fd

    @staticmethod
    def _release(fd: Optional[int]) -> None:
        if fd is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def _lock_is_free(self) -> bool:
        fd = self._try_lock()
        if fd is None:
            return False
        self._release(fd)
        return True

    # ---- 読み出し
    def get(self, jid) -> dict:
        jid = str(jid or "")
        if not re.fullmatch(rf"{self.prefix}\d+", jid):
            raise KeyError(jid)
        job = store.read_json(self._path(jid), None)
        if job is None:
            raise KeyError(jid)
        if job.get("status") == "running" and jid not in self._active and self._lock_is_free():
            job["status"] = "error"
            job["error"] = "実行が途中で止まりました（アプリの再起動など）。もう一度やり直してください。"
        return job

    def list(self, limit: int = 30) -> List[dict]:
        d = self.dir()
        if not d.is_dir():
            return []
        out = []
        for p in sorted(d.glob(f"{self.prefix}*.json"), key=lambda p: _num(p.stem), reverse=True)[:limit]:
            try:
                out.append(self.get(p.stem))
            except KeyError:
                continue
        return out

    def running(self) -> Optional[dict]:
        for j in self.list(5):
            if j.get("status") == "running":
                return j
        return None

    # ---- 実行
    def _begin(self, p: dict, confirm_token: str) -> Tuple[dict, int]:
        if not confirm_token or confirm_token != p.get("confirm_token"):
            raise ConfirmMismatch("確認した内容と、いま送る内容が違います（材料が変わった可能性があります）。"
                                  "もう一度確認画面からやり直してください。")
        with self._lock:
            if self._active:
                raise ValueError(self.busy)
            fd = self._try_lock()
            if fd is None:
                raise ValueError(self.busy)
            try:
                with _locked():
                    d = self.dir()
                    existing = [_num(x.stem) for x in d.glob(f"{self.prefix}*.json")]
                    jid = _alloc_id(self.prefix, max(existing + [0]))
                    job = {"id": jid, "kind": self.kind, "status": "running", "created": store.now(), "finished": "",
                           "prompt_chars": len(p.get("prompt", "")), "model": "", "error": "", "limit_resets_at": ""}
                    job.update(p.get("job") or {})
                    self.save(job)
                self._active.add(jid)
            except BaseException:
                self._release(fd)
                raise
        return job, fd

    def _finish(self, job: dict, fd: int) -> dict:
        job["finished"] = store.now()
        try:
            self.save(job)
        finally:
            with self._lock:
                self._active.discard(job["id"])
                self._release(fd)
        return job

    def _execute(self, job: dict, prompt: str, handler: Callable[[dict, str], None], fd: int) -> dict:
        try:
            cfg = claude_config()
            res = run_claude(prompt, (), max_turns=int(cfg.get("max_turns") or 2),
                             timeout_sec=int(cfg.get("timeout_sec") or 600), cwd=self.dir() / ".claude-cwd",
                             model=str(cfg.get("model") or ""), command=str(cfg.get("command") or "claude"),
                             disallowed_tools=tuple(cfg.get("disallowed_tools") or ()),
                             limit_patterns=tuple(cfg.get("limit_patterns") or ()))
            job["model"] = res.model or str(cfg.get("model") or "") or "（既定のモデル）"
            if not res.text.strip():
                raise ClaudeRunError("Claude から空の結果が返りました。")
            job["status"] = "done"
            handler(job, res.text)
        except ClaudeLimitError as ex:
            job["status"] = "limit"
            job["limit_resets_at"] = getattr(ex, "resets_at", "") or ""
            job["error"] = limit_message(job["limit_resets_at"])
        except (ClaudeRunError, ValueError, store.VaultError, OSError, KeyError) as ex:
            job["status"] = "error"
            job["error"] = secrets.redact(str(ex))
        except Exception as ex:  # 想定外でもジョブは必ず終わらせる
            job["status"] = "error"
            job["error"] = secrets.redact(f"予期しないエラー（{type(ex).__name__}）: {ex}")
        return self._finish(job, fd)

    def start(self, p: dict, confirm_token: str, handler: Callable[[dict, str], None]) -> str:
        """確認済みの内容 p（prompt・confirm_token・job に入れる項目）を裏で実行する。"""
        job, fd = self._begin(p, confirm_token)
        t = threading.Thread(target=self._execute, args=(job, p["prompt"], handler, fd),
                             name=f"{self.kind}-{job['id']}", daemon=True)
        try:
            t.start()
        except BaseException as ex:
            job["status"] = "error"
            job["error"] = f"実行を開始できませんでした（{type(ex).__name__}）。"
            self._finish(job, fd)
            raise
        return job["id"]

    def run(self, p: dict, confirm_token: str, handler: Callable[[dict, str], None]) -> dict:
        job, fd = self._begin(p, confirm_token)
        return self._execute(job, p["prompt"], handler, fd)


# ---------------------------------------------------------------- 応答の読み取り・長い一致の検出

_FENCE = re.compile(r"```(?:json|JSON)?\s*\n(.*?)\n?```", re.S)


def json_obj(text: str) -> dict:
    import json
    t = (text or "").strip()
    m = _FENCE.search(t)
    if m:
        t = m.group(1).strip()
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("Claude の返答から JSON を読み取れませんでした。")
    try:
        data = json.loads(t[start:end + 1])
    except ValueError:
        raise ValueError("Claude の返答の JSON が壊れていました。") from None
    if not isinstance(data, dict):
        raise ValueError("Claude の返答の形が想定と違いました。")
    return data


def _squash(s: str) -> str:
    import unicodedata
    return re.sub(r"[\s　]+", "", unicodedata.normalize("NFKC", s or ""))


def longest_common(text: str, source: str) -> str:
    """text のうち source にそのまま含まれる最長の部分（空白と全角半角の違いは無視）。複製の機械チェック用。"""
    a, b = _squash(text), _squash(source)
    if not a or not b:
        return ""
    lo, hi, best = 0, len(a), ""
    while lo < hi:  # 長さ L の一致があれば L-1 もある（単調）ので二分探索
        mid = (lo + hi + 1) // 2
        hit = next((a[i:i + mid] for i in range(len(a) - mid + 1) if a[i:i + mid] in b), "")
        if hit:
            lo, best = mid, hit
        else:
            hi = mid - 1
    return best


def fill(tpl: str, values: Dict[str, str]) -> str:
    """{key} を1回の置き換えで入れる（材料に {key} の文字があっても二重に置き換わらない）。"""
    keys = "|".join(re.escape(k) for k in values)
    return re.sub(r"\{(" + keys + r")\}", lambda m: values[m.group(1)], tpl)


def template(fname: str) -> str:
    return (store.CONFIG_DIR / "writing_prompts" / fname).read_text(encoding="utf-8")
