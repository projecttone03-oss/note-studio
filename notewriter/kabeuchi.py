"""壁打ちセッションのランチャー（SPEC.md 機能4・絶対4）。

記事ごとに1つ、`claude --remote-control <名前>`（対話セッション方式）で常駐させ、スマホの Claude アプリから続ける。
- サーバーモード（`claude remote-control`）は使わない。`--bare` も使わない（サブスク認証が使えなくなるため）。
- 起動フラグは本文確定と同じツール制限（claude_runner.safety_flags: --tools "" ・--disallowedTools・
  PreToolUse フック tool_guard.py・--strict-mcp-config・--setting-sources ""）。
- 対話セッションの system/init は直接読めないので、起動の直前に**同じフラグと同じ材料ファイルで -p の確認実行**をし、
  init のツール一覧が空・MCP なし・権限モード dontAsk であることを確かめてから起動する。確かめられなければ起動しない。
- かけら等の材料はコマンドライン引数に入れない。作業フォルダ（暗号化境界）の中に材料ファイルを作り
  `--append-system-prompt-file` で渡す。確認実行と起動で材料ファイルが同じ（ハッシュ一致）ことも確かめる。
- 常駐は tmux（あれば）。tmux がなければ、CLI では今の端末でそのまま起動する。
- 対話セッションの履歴は ~/.claude/projects/ に残る（--no-session-persistence は -p 専用）。
  VPS ではこのフォルダも暗号化境界に入れること（notewriter/ops/ の手順書）。

保存先: 作業フォルダの kabeuchi/<記事>/system.md（材料）・preflight.json（確認実行の記録）・cwd/（作業ディレクトリ）
"""
from __future__ import annotations

import hashlib
import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from . import kakera as K
from . import jobs, store
from . import writing as W
from .claude_runner import base_command, run_claude, safety_flags

DEFAULTS = {
    "remote_control_args": ["--remote-control", "{name}"],
    "name_prefix": "nw-",
    "tmux": "tmux",
    "preflight_prompt": "起動前の確認です。「OK」とだけ返してください。",
    "preflight_timeout_sec": 180,
    "material_max_chars": 60000,
    "include_draft": True,
}


def config() -> dict:
    cfg = dict(DEFAULTS)
    cfg.update(store.read_json(store.CONFIG_DIR / "kabeuchi.json", {}) or {})
    try:
        cfg.update(store.read_json(store.vault() / "config" / "kabeuchi.json", {}) or {})
    except store.VaultError:
        pass
    return cfg


def _dir(article: str) -> Path:
    return store.vault() / "kabeuchi" / W.slug(article)


def material_path(article: str) -> Path:
    return _dir(article) / "system.md"


def session_name(article: str) -> str:
    """Remote Control でスマホに表示される名前。"""
    return config()["name_prefix"] + W.slug(article)


def tmux_name(article: str) -> str:
    """tmux のセッション名（記号や日本語で困らないよう、記事名のハッシュ）。"""
    return "nwk-" + hashlib.sha1(W._article(article)["name"].encode("utf-8")).hexdigest()[:10]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------- 材料

def build_material(article: str) -> str:
    a = W._article(article)
    cfg = config()
    ks = [k for k in K.list_kakera() if k["article"] == a["name"] and k["status"] != W.EXCLUDED_STATUS]
    parts = []
    for k in ks:
        vp = "、".join(k["viewpoints"]) or "—"
        parts.append(f"[{k['id']}]（区間: {k['section'] or '未設定'}／観点: {vp}）\n{k['body'].strip()}")
    draft = "（まだありません）"
    if cfg.get("include_draft") and W.current(a["name"]):
        draft = W.render(W.current_blocks(a["name"])).strip()
    text = jobs.fill(jobs.template("kabeuchi.md"), {
        "article": a["name"], "sections": " → ".join(a["sections"]) or "（未設定）",
        "kakera": "\n\n".join(parts) or "（まだありません）", "draft": draft,
        "style": W._style_for_prompt(W.load_config())})
    limit = int(cfg.get("material_max_chars") or 60000)
    if len(text) > limit:
        raise ValueError(f"壁打ちの材料が長すぎます（{len(text)}字 / 上限{limit}字）。途中で切ると事実が抜けるため起動しません。"
                         "config/kabeuchi.json の material_max_chars を見直すか、下書きを含めない設定にしてください。")
    return text


def write_material(article: str) -> Path:
    p = material_path(article)
    store.atomic_write(p, build_material(article))
    try:
        os.chmod(str(p), 0o600)
    except OSError:
        pass
    (_dir(article) / "cwd").mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------- コマンド

def _claude_cfg() -> dict:
    return W.load_config()


def launch_argv(article: str) -> List[str]:
    """対話セッション（Remote Control）を起動するコマンドライン。材料はファイルのパスだけを渡す。"""
    cfg, wcfg = config(), _claude_cfg()
    name = session_name(article)
    rc = [str(x).replace("{name}", name) for x in cfg["remote_control_args"]]
    argv = base_command(str(wcfg.get("command") or "claude")) + rc
    argv += safety_flags((), tuple(wcfg.get("disallowed_tools") or ()))
    argv += ["--append-system-prompt-file", str(material_path(article))]
    if wcfg.get("model"):
        argv += ["--model", str(wcfg["model"])]
    _assert_safe(argv)
    return argv


def _assert_safe(argv: List[str]) -> None:
    if "--bare" in argv:
        raise ValueError("--bare は使いません（サブスク認証が使えなくなるため）。")
    if "remote-control" in argv:  # サブコマンド（サーバーモード）
        raise ValueError("サーバーモード（claude remote-control）は使いません。対話セッション方式（--remote-control）にしてください。")
    if "--tools" not in argv or argv[argv.index("--tools") + 1] != "":
        raise ValueError("ツールをすべて拒否するフラグ（--tools \"\"）がありません。")


def launch_env() -> Dict[str, str]:
    env = dict(os.environ)
    env["NW_ALLOWED_TOOLS"] = ""  # PreToolUse フック: 空 = すべて拒否
    env.pop("PERPLEXITY_API_KEY", None)
    return env


# ---------------------------------------------------------------- 確認実行

def preflight(article: str) -> dict:
    """起動と同じフラグ・同じ材料ファイルで -p の確認実行をし、ツール0・MCPなしを確かめる。失敗なら例外。"""
    name = W._article(article)["name"]
    path = write_material(name)
    wcfg, cfg = _claude_cfg(), config()
    res = run_claude(str(cfg["preflight_prompt"]), (), max_turns=1,
                     timeout_sec=int(cfg.get("preflight_timeout_sec") or 180), cwd=_dir(name) / "cwd",
                     model=str(wcfg.get("model") or ""), command=str(wcfg.get("command") or "claude"),
                     disallowed_tools=tuple(wcfg.get("disallowed_tools") or ()),
                     limit_patterns=tuple(wcfg.get("limit_patterns") or ()),
                     extra_args=["--append-system-prompt-file", str(path)])
    rec = {"at": store.now(), "ok": True, "tools": res.init_tools, "model": res.model, "material_sha256": _sha(path)}
    store.write_json(_dir(name) / "preflight.json", rec)
    return rec


def last_preflight(article: str) -> Optional[dict]:
    return store.read_json(_dir(article) / "preflight.json", None)


# ---------------------------------------------------------------- tmux

def tmux_path() -> Optional[str]:
    cmd = os.environ.get("NW_TMUX_CMD") or str(config().get("tmux") or "tmux")
    return shutil.which(shlex.split(cmd)[0]) and cmd or None


def _tmux(*args: str) -> subprocess.CompletedProcess:
    cmd = tmux_path()
    if not cmd:
        raise ValueError("tmux が見つかりません。")
    return subprocess.run(shlex.split(cmd) + list(args), capture_output=True, text=True, timeout=20)


def is_running(article: str) -> bool:
    if not tmux_path():
        return False
    return _tmux("has-session", "-t", tmux_name(article)).returncode == 0


def start(article: str, use_tmux: bool = True) -> dict:
    """確認実行 → 材料が同じか確認 → 起動。tmux がなければ起動用の情報だけ返す（CLI がその場で起動する）。"""
    name = W._article(article)["name"]
    if use_tmux and tmux_path() and is_running(name):
        raise ValueError(f"「{name}」の壁打ちはもう起動しています（記事ごとに1つ）。止めてから起動し直してください。")
    rec = preflight(name)
    if _sha(material_path(name)) != rec["material_sha256"]:
        raise ValueError("確認実行のあとで材料ファイルが変わったため、起動しませんでした。")
    argv = launch_argv(name)
    cwd = str(_dir(name) / "cwd")
    info = {"article": name, "session": session_name(name), "argv": argv, "cwd": cwd, "preflight": rec, "tmux": False}
    if use_tmux and tmux_path():
        shell = "cd " + shlex.quote(cwd) + " && exec env NW_ALLOWED_TOOLS= " + " ".join(shlex.quote(x) for x in argv)
        r = _tmux("new-session", "-d", "-s", tmux_name(name), shell)
        if r.returncode != 0:
            raise ValueError("tmux で起動できませんでした: " + (r.stderr or "").strip()[:200])
        info["tmux"] = True
    return info


def stop(article: str) -> bool:
    name = W._article(article)["name"]
    if not tmux_path() or not is_running(name):
        return False
    _tmux("kill-session", "-t", tmux_name(name))
    return True


def overview() -> List[dict]:
    out = []
    for a in K.list_articles():
        out.append({"article": a["name"], "session": session_name(a["name"]), "running": is_running(a["name"]),
                    "preflight": last_preflight(a["name"])})
    return out
