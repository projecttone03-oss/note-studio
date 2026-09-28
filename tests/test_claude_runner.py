"""notewriter の Claude Code 実行部（claude_runner）とフック（hooks/tool_guard.py）を確認する。

本物の claude は呼ばない。NW_CLAUDE_CMD にテスト内で作った偽の claude（stream-json を出す Python）を指定する。
実行: python3 -m unittest discover tests
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from notewriter import claude_runner, store
from notewriter.claude_runner import ClaudeLimitError, ClaudeRunError, ToolVerificationError, run_claude

FAKE = textwrap.dedent('''
    import json, os, sys, time
    mode = os.environ.get("FAKE_MODE", "ok")
    out_dir = os.environ["FAKE_OUT"]
    prompt = sys.stdin.read()
    with open(os.path.join(out_dir, "argv.json"), "w", encoding="utf-8") as f:
        json.dump({"argv": sys.argv[1:], "stdin": prompt, "allowed": os.environ.get("NW_ALLOWED_TOOLS"),
                   "has_pplx": "PERPLEXITY_API_KEY" in os.environ}, f, ensure_ascii=False)
    def emit(obj):
        print(json.dumps(obj, ensure_ascii=False), flush=True)
    tools = ["WebFetch", "WebSearch"]
    mcp = []
    if mode == "bash":
        tools = tools + ["Bash"]
    if mode == "mcp":
        mcp = [{"name": "x", "status": "connected"}]
    if mode == "sleep":
        time.sleep(30)
    if mode != "noinit":
        emit({"type": "system", "subtype": "init", "tools": tools, "mcp_servers": mcp,
              "permissionMode": "dontAsk", "model": "claude-fake"})
    if mode == "rejected":
        emit({"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "resetsAt": 1900000000}})
        time.sleep(30)
    emit({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "resetsAt": 1900000000}})
    if mode == "limit_text":
        emit({"type": "result", "subtype": "success", "is_error": True, "result": "Claude usage limit reached"})
    elif mode == "error":
        emit({"type": "result", "subtype": "success", "is_error": True, "result": "something broke"})
    else:
        emit({"type": "result", "subtype": "success", "is_error": False, "result": "答え: " + prompt[:10],
              "duration_ms": 1234,
              "usage": {"input_tokens": 5, "server_tool_use": {"web_search_requests": 2, "web_fetch_requests": 1}}})
''')


class ClaudeRunnerTest(unittest.TestCase):
    def setUp(self):
        self._old = {k: os.environ.get(k) for k in ("NW_DATA_DIR", "NW_CLAUDE_CMD", "FAKE_MODE", "FAKE_OUT",
                                                     "PERPLEXITY_API_KEY")}
        self.tmp = Path(tempfile.mkdtemp(prefix="notewriter-claude-"))
        os.environ["NW_DATA_DIR"] = str(self.tmp / "vault")
        os.environ.pop("PERPLEXITY_API_KEY", None)
        store.init_vault()
        self.script = self.tmp / "fake_claude.py"
        self.script.write_text(FAKE, encoding="utf-8")
        os.environ["NW_CLAUDE_CMD"] = sys.executable + " " + str(self.script)
        os.environ["FAKE_OUT"] = str(self.tmp)
        self.cwd = self.tmp / "vault" / "research" / ".claude-cwd"

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_fake(self, mode="ok", tools=("WebSearch", "WebFetch"), timeout=20, prompt="秘密ではない質問文"):
        os.environ["FAKE_MODE"] = mode
        return run_claude(prompt, tools, max_turns=5, timeout_sec=timeout, cwd=self.cwd,
                          disallowed_tools=("Bash", "Read", "mcp__*"), limit_patterns=("(?i)usage limit", "上限"))

    def recorded(self):
        return json.loads((self.tmp / "argv.json").read_text(encoding="utf-8"))

    def test_ok_and_command_line(self):
        os.environ["PERPLEXITY_API_KEY"] = "pplx-shouldNotLeak"
        res = self.run_fake(prompt="プロンプト本文ABCDEFG")
        self.assertEqual(res.text, "答え: プロンプト本文ABC")
        self.assertEqual(sorted(res.init_tools), ["WebFetch", "WebSearch"])
        self.assertEqual((res.web_search_requests, res.web_fetch_requests), (2, 1))
        self.assertEqual(res.duration_ms, 1234)
        self.assertEqual(res.model, "claude-fake")
        rec = self.recorded()
        argv = rec["argv"]
        self.assertEqual(rec["stdin"], "プロンプト本文ABCDEFG")
        self.assertFalse(any("プロンプト本文" in a for a in argv))
        self.assertNotIn("--bare", argv)
        for flag in ("--strict-mcp-config", "--tools", "--settings", "--no-session-persistence", "-p"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "dontAsk")
        self.assertEqual(argv[argv.index("--setting-sources") + 1], "")
        self.assertEqual(argv[argv.index("--tools") + 1], "WebSearch,WebFetch")
        self.assertEqual(argv[argv.index("--allowedTools") + 1], "WebSearch,WebFetch")
        self.assertIn("Bash", argv[argv.index("--disallowedTools") + 1])
        settings = json.loads(argv[argv.index("--settings") + 1])
        hook = settings["hooks"]["PreToolUse"][0]
        self.assertEqual(hook["matcher"], "*")
        self.assertIn("tool_guard.py", hook["hooks"][0]["command"])
        self.assertEqual(rec["allowed"], "WebSearch,WebFetch")
        self.assertFalse(rec["has_pplx"])  # APIキーは子プロセスに渡さない
        self.assertTrue(self.cwd.is_dir())

    def test_no_tools_mode(self):
        with self.assertRaises(ToolVerificationError):
            self.run_fake(tools=())  # 偽 claude は WebSearch 等を報告する＝想定（なし）と違う
        argv = self.recorded()["argv"]
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertNotIn("--allowedTools", argv)
        self.assertEqual(self.recorded()["allowed"], "")

    def test_bash_in_init_is_rejected(self):
        with self.assertRaises(ToolVerificationError):
            self.run_fake("bash")

    def test_mcp_in_init_is_rejected(self):
        with self.assertRaises(ToolVerificationError):
            self.run_fake("mcp")

    def test_no_init_is_rejected(self):
        with self.assertRaises(ToolVerificationError):
            self.run_fake("noinit")

    def test_rate_limit_rejected(self):
        with self.assertRaises(ClaudeLimitError) as cm:
            self.run_fake("rejected")
        self.assertTrue(cm.exception.resets_at)

    def test_limit_text_in_result(self):
        with self.assertRaises(ClaudeLimitError):
            self.run_fake("limit_text")

    def test_other_error(self):
        with self.assertRaises(ClaudeRunError) as cm:
            self.run_fake("error")
        self.assertNotIsInstance(cm.exception, ClaudeLimitError)

    def test_timeout(self):
        with self.assertRaises(ClaudeRunError) as cm:
            self.run_fake("sleep", timeout=1)
        self.assertIn("秒", str(cm.exception))

    def test_command_not_found(self):
        os.environ["NW_CLAUDE_CMD"] = str(self.tmp / "no-such-claude")
        with self.assertRaises(ClaudeRunError) as cm:
            self.run_fake()
        self.assertIn("claude コマンドが見つかりません", str(cm.exception))


class ToolGuardTest(unittest.TestCase):
    def guard(self, allowed, payload):
        env = dict(os.environ)
        env["NW_ALLOWED_TOOLS"] = allowed
        data = payload if isinstance(payload, str) else json.dumps(payload)
        return subprocess.run([sys.executable, str(claude_runner.HOOK_SCRIPT)], input=data, env=env,
                              capture_output=True, text=True, timeout=30)

    def test_guard(self):
        self.assertEqual(self.guard("WebSearch,WebFetch", {"tool_name": "WebSearch"}).returncode, 0)
        self.assertEqual(self.guard("WebSearch,WebFetch", {"tool_name": "WebFetch"}).returncode, 0)
        r = self.guard("WebSearch,WebFetch", {"tool_name": "Bash", "tool_input": {"command": "ls"}})
        self.assertEqual(r.returncode, 2)
        self.assertIn("Bash", r.stderr)
        self.assertEqual(self.guard("WebSearch,WebFetch", {"tool_name": "mcp__x__y"}).returncode, 2)
        for name in ("WebSearch", "Read", ""):
            self.assertEqual(self.guard("", {"tool_name": name}).returncode, 2)
        self.assertEqual(self.guard("WebSearch", "not json").returncode, 2)
        self.assertEqual(self.guard("WebSearch", "[]").returncode, 2)


if __name__ == "__main__":
    unittest.main()
