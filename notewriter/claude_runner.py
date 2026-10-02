"""Claude Code CLI（サブスク認証の claude -p）を、使えるツールを絞った別セッションで実行する。

リサーチ（WebSearch・WebFetch だけ）にも、ネタ出し（ツールなし）にも使う汎用の実行部。
- プロンプトは標準入力で渡す（コマンドライン引数に入れない＝ps 等に出ない）。
- ツールの制限は3重: --tools / --allowedTools / --disallowedTools のフラグ、PreToolUse フック
  （notewriter/hooks/tool_guard.py。許可は環境変数 NW_ALLOWED_TOOLS）、
  起動直後の system/init を検証して想定と違えば即停止。
- --strict-mcp-config と --setting-sources "" で利用者の MCP・設定・フックを読まない。
  --no-session-persistence で履歴を残さない。--bare は使わない（サブスク認証が使えなくなるため）。
- 結果は標準出力（stream-json）で受け取り、保存は呼び出し側が行う。
"""
from __future__ import annotations

import json
import os
import queue
import re
import shlex
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence

from . import secrets

HOOK_SCRIPT = Path(__file__).resolve().parent / "hooks" / "tool_guard.py"
OK_RATE_STATUSES = ("allowed", "allowed_warning")
# 子プロセスに渡さない環境変数（APIキー等）
_SCRUB_ENV = ("PERPLEXITY_API_KEY",)


class ClaudeRunError(RuntimeError):
    """claude の実行に失敗した（コマンドなし・タイムアウト・エラー終了など）。"""


class ToolVerificationError(ClaudeRunError):
    """起動したセッションのツール構成が想定と違った（出力は捨てた）。"""


class ClaudeLimitError(ClaudeRunError):
    """Claude の利用上限に達した。resets_at は表示用の日時文字列（不明なら ""）。"""

    def __init__(self, message: str, resets_at: str = ""):
        super().__init__(message)
        self.resets_at = resets_at


@dataclass
class ClaudeResult:
    text: str
    init_tools: List[str] = field(default_factory=list)
    web_search_requests: int = 0
    web_fetch_requests: int = 0
    duration_ms: int = 0
    raw_usage: dict = field(default_factory=dict)
    model: str = ""  # init で報告されたモデル名（契約への追加項目）


def _fmt_reset(v) -> str:
    try:
        ts = float(v)
    except (TypeError, ValueError):
        return ""
    if ts <= 0:
        return ""
    if ts > 1e12:  # ミリ秒で来た場合
        ts /= 1000.0
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except (OverflowError, OSError, ValueError):
        return ""


def base_command(command: str = "claude") -> List[str]:
    """claude 本体の起動部分（環境変数 NW_CLAUDE_CMD があればそちら。テストの偽 claude 用）。"""
    override = os.environ.get("NW_CLAUDE_CMD")
    return shlex.split(override) if override else shlex.split(command or "claude")


def safety_flags(allowed_tools: Sequence[str], disallowed_tools: Sequence[str] = ()) -> List[str]:
    """ツール制限のフラグ（単発の -p 実行と、壁打ちの対話セッションで同じものを使う）。

    --tools / --allowedTools / --disallowedTools、権限モード dontAsk、MCP なし（--strict-mcp-config）、
    利用者の設定・フックを読まない（--setting-sources ""）、PreToolUse フック（tool_guard.py）。
    """
    allowed = [t for t in allowed_tools if t]
    hook_cmd = shlex.quote(sys.executable) + " " + shlex.quote(str(HOOK_SCRIPT))
    settings = {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": hook_cmd}]}]}}
    cmd = ["--tools", ",".join(allowed)]
    if allowed:
        cmd += ["--allowedTools", ",".join(allowed)]
    deny = [t for t in disallowed_tools if t and t not in allowed]
    if deny:
        cmd += ["--disallowedTools", ",".join(deny)]
    cmd += [
        "--permission-mode", "dontAsk",
        "--strict-mcp-config",
        "--setting-sources", "",
        "--settings", json.dumps(settings, ensure_ascii=False),
    ]
    return cmd


def build_command(allowed_tools: Sequence[str], *, max_turns: int, model: str = "", command: str = "claude",
                  disallowed_tools: Sequence[str] = (), extra_args: Sequence[str] = ()) -> List[str]:
    """実行するコマンドライン（プロンプトは含まない）。"""
    cmd = base_command(command) + ["-p", "--output-format", "stream-json", "--verbose"]
    cmd += safety_flags(allowed_tools, disallowed_tools)
    cmd += ["--no-session-persistence", "--max-turns", str(int(max_turns))]
    cmd += list(extra_args)
    if model:
        cmd += ["--model", model]
    return cmd


def _reader(stream, q: "queue.Queue", tag: str) -> None:
    try:
        for line in iter(stream.readline, ""):
            q.put((tag, line))
    except (ValueError, OSError):
        pass
    finally:
        q.put((tag, None))


def _kill(proc: subprocess.Popen) -> None:
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def _matches(patterns: Sequence[str], text: str) -> bool:
    for p in patterns or ():
        try:
            if re.search(p, text or ""):
                return True
        except re.error:
            continue
    return False


def run_claude(prompt: str, allowed_tools: Sequence[str], *, max_turns: int, timeout_sec: int, cwd: Path,
               model: str = "", command: str = "claude", disallowed_tools: Sequence[str] = (),
               limit_patterns: Sequence[str] = (), extra_args: Sequence[str] = ()) -> ClaudeResult:
    allowed = [t for t in allowed_tools if t]
    cmd = build_command(allowed, max_turns=max_turns, model=model, command=command,
                        disallowed_tools=disallowed_tools, extra_args=extra_args)
    env = {k: v for k, v in os.environ.items() if k not in _SCRUB_ENV}
    env["NW_ALLOWED_TOOLS"] = ",".join(allowed)
    cwd = Path(cwd)
    cwd.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                cwd=str(cwd), env=env, text=True, encoding="utf-8", errors="replace", bufsize=1)
    except FileNotFoundError:
        raise ClaudeRunError("claude コマンドが見つかりません。Claude Code をインストールしてログインしてください"
                             "（設定の command か環境変数 NW_CLAUDE_CMD でも指定できます）。") from None
    except OSError as e:
        raise ClaudeRunError(secrets.redact(f"claude を起動できませんでした: {e}")) from None

    q: "queue.Queue" = queue.Queue()
    stderr_buf: List[str] = []

    def _feed() -> None:
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
        except (BrokenPipeError, OSError, ValueError):
            pass

    threads = [threading.Thread(target=_feed, daemon=True),
               threading.Thread(target=_reader, args=(proc.stdout, q, "out"), daemon=True),
               threading.Thread(target=_reader, args=(proc.stderr, q, "err"), daemon=True)]
    for t in threads:
        t.start()

    deadline = started + max(1, int(timeout_sec))
    init: Optional[dict] = None
    result: Optional[dict] = None
    open_streams = 2
    try:
        while open_streams:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _kill(proc)
                raise ClaudeRunError(f"claude の実行が {int(timeout_sec)} 秒を超えたため止めました。")
            try:
                tag, line = q.get(timeout=min(remaining, 1.0))
            except queue.Empty:
                continue
            if line is None:
                open_streams -= 1
                continue
            if tag == "err":
                if sum(len(x) for x in stderr_buf) < 20000:
                    stderr_buf.append(line)
                continue
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if not isinstance(ev, dict):
                continue
            typ = ev.get("type")
            if typ == "system" and ev.get("subtype") == "init":
                init = ev
                tools = ev.get("tools")
                problems = []
                if not isinstance(tools, list) or set(map(str, tools)) != set(allowed):
                    problems.append("使えるツールが想定と違います（想定: " + (", ".join(sorted(allowed)) or "なし")
                                    + " / 実際: " + (", ".join(sorted(map(str, tools or []))) or "なし") + "）")
                if ev.get("mcp_servers"):
                    problems.append("MCP サーバーが読み込まれています")
                if ev.get("permissionMode") != "dontAsk":
                    problems.append("権限モードが dontAsk ではありません（" + str(ev.get("permissionMode")) + "）")
                if problems:
                    _kill(proc)
                    raise ToolVerificationError("安全のため実行を止めました（出力は破棄）: " + "。".join(problems)
                                                + "。Claude Code の版や設定を確認してください。")
            elif typ == "rate_limit_event":
                info = ev.get("rate_limit_info") or {}
                status = str(info.get("status") or "")
                if status and status not in OK_RATE_STATUSES:
                    _kill(proc)
                    resets = _fmt_reset(info.get("resetsAt"))
                    msg = "Claude の利用上限に達しました"
                    msg += f"（{resets} ごろ回復見込み）。" if resets else "。"
                    raise ClaudeLimitError(msg, resets)
            elif typ == "result":
                result = ev
                if init is None:
                    _kill(proc)
                    raise ToolVerificationError("安全のため実行を止めました（出力は破棄）: 起動時のツール構成（init）を"
                                                "確認できないまま結果が返りました。")
        try:
            proc.wait(timeout=max(1.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            _kill(proc)
    finally:
        if proc.poll() is None:
            _kill(proc)
        for t in threads:
            t.join(timeout=2)
        for s in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if s is not None:
                    s.close()
            except (OSError, ValueError):
                pass

    stderr_text = "".join(stderr_buf)
    if result is None:
        if init is None and proc.returncode not in (0, None) and _matches(limit_patterns, stderr_text):
            raise ClaudeLimitError("Claude の利用上限に達した可能性があります。")
        tail = secrets.redact(stderr_text.strip()[-400:])
        if init is None and not tail:
            raise ToolVerificationError("安全のため結果を使いません: 起動時のツール構成（init）を確認できませんでした。")
        raise ClaudeRunError(f"claude が結果を返さずに終了しました（終了コード {proc.returncode}）。"
                             + (f" {tail}" if tail else ""))

    text = str(result.get("result") or "")
    if result.get("is_error"):
        if _matches(limit_patterns, text):
            raise ClaudeLimitError("Claude の利用上限に達しました。" + secrets.redact(text[:200]))
        raise ClaudeRunError("claude がエラーを返しました: " + (secrets.redact(text[:400]) or str(result.get("subtype"))))
    if result.get("subtype") not in (None, "success"):
        sub = str(result.get("subtype"))
        if sub == "error_max_turns":
            raise ClaudeRunError("最大ターン数に達したため、結果が完成しませんでした（設定の max_turns を増やせます）。")
        raise ClaudeRunError(f"claude が正常に終わりませんでした（{sub}）。")
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    stu = usage.get("server_tool_use") if isinstance(usage.get("server_tool_use"), dict) else {}

    def _int(v) -> int:
        try:
            return int(v or 0)
        except (TypeError, ValueError):
            return 0

    duration = _int(result.get("duration_ms")) or int((time.monotonic() - started) * 1000)
    return ClaudeResult(text=text, init_tools=list(map(str, (init or {}).get("tools") or [])),
                        web_search_requests=_int(stu.get("web_search_requests")),
                        web_fetch_requests=_int(stu.get("web_fetch_requests")),
                        duration_ms=duration, raw_usage=usage, model=str((init or {}).get("model") or ""))
