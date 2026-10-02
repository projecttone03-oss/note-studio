"""壁打ちランチャー（kabeuchi）の確認。偽の claude と偽の tmux だけを使う（本物は起動しない）。"""
import json
import os
import shlex
import sys
import textwrap
import urllib.parse

from notewriter import kabeuchi, store
from tests.test_writing import WritingBase, ART
from tests.webutil import ServerMixin

FAKE_TMUX = textwrap.dedent('''
    import json, os, sys
    d = os.environ["FAKE_TMUX_DIR"]
    args = sys.argv[1:]
    with open(os.path.join(d, "log.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(args, ensure_ascii=False) + "\\n")
    mark = os.path.join(d, "running-" + (args[args.index("-t") + 1] if "-t" in args else args[args.index("-s") + 1]))
    if args[0] == "has-session":
        sys.exit(0 if os.path.exists(mark) else 1)
    if args[0] == "new-session":
        open(mark, "w").close()
    if args[0] == "kill-session" and os.path.exists(mark):
        os.unlink(mark)
''')


class KabeuchiBase(WritingBase):
    ENV = WritingBase.ENV + ("NW_TMUX_CMD", "FAKE_TMUX_DIR")

    def setUp(self):
        super().setUp()
        self.tmux_dir = self.tmp / "tmux"
        self.tmux_dir.mkdir()
        script = self.tmp / "fake_tmux.py"
        script.write_text(FAKE_TMUX, encoding="utf-8")
        os.environ["NW_TMUX_CMD"] = sys.executable + " " + str(script)
        os.environ["FAKE_TMUX_DIR"] = str(self.tmux_dir)
        self.set_result("OK")

    def tmux_log(self):
        p = self.tmux_dir / "log.jsonl"
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []

class KabeuchiTest(KabeuchiBase):
    def test_preflight_then_launch_with_same_flags(self):
        info = kabeuchi.start(ART)
        self.assertTrue(info["tmux"])
        pre = self.last_call()
        self.assertEqual(pre["allowed"], "", "確認実行もツールなし")
        self.assertNotIn("目覚まし", " ".join(pre["argv"]), "かけらは引数に出さない")
        material = kabeuchi.material_path(ART)
        self.assertIn("目覚ましが鳴る前に", material.read_text(encoding="utf-8"))
        self.assertNotIn("保留中の秘密メモ", material.read_text(encoding="utf-8"), "保留のかけらは渡さない")
        self.assertIn(str(material), pre["argv"])
        argv = info["argv"]
        self.assertIn("--remote-control", argv)
        self.assertEqual(argv[argv.index("--remote-control") + 1], "nw-" + ART)
        self.assertNotIn("--bare", argv)
        self.assertNotIn("remote-control", argv, "サーバーモードは使わない")
        self.assertNotIn("-p", argv)
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        # 確認実行と同じツール制限のフラグ
        for flag in ("--disallowedTools", "--permission-mode", "--strict-mcp-config", "--setting-sources", "--settings"):
            self.assertIn(flag, argv)
            self.assertIn(flag, pre["argv"])
        for flag in ("--disallowedTools", "--settings", "--permission-mode"):
            self.assertEqual(argv[argv.index(flag) + 1], pre["argv"][pre["argv"].index(flag) + 1])
        self.assertNotIn("目覚まし", " ".join(argv))
        new = [a for a in self.tmux_log() if a[0] == "new-session"][0]
        self.assertIn("NW_ALLOWED_TOOLS=", new[-1])
        self.assertEqual(shlex.split(new[-1].split("exec env NW_ALLOWED_TOOLS= ", 1)[1]), argv)
        self.assertTrue(kabeuchi.is_running(ART))
        with self.assertRaises(ValueError):
            kabeuchi.start(ART)  # 記事ごとに1つ
        self.assertTrue(kabeuchi.stop(ART))
        self.assertFalse(kabeuchi.is_running(ART))

    def test_tools_in_preflight_block_launch(self):
        os.environ["FAKE_MODE"] = "tools"
        with self.assertRaises(Exception) as cm:
            kabeuchi.start(ART)
        self.assertIn("安全のため", str(cm.exception))
        self.assertEqual([a for a in self.tmux_log() if a[0] == "new-session"], [], "確かめられなければ起動しない")
        self.assertIsNone(kabeuchi.last_preflight(ART))

    def test_without_tmux_returns_command(self):
        os.environ["NW_TMUX_CMD"] = "/nonexistent/tmux"
        info = kabeuchi.start(ART)
        self.assertFalse(info["tmux"])
        self.assertIn("--append-system-prompt-file", info["argv"])
        self.assertEqual(kabeuchi.launch_env()["NW_ALLOWED_TOOLS"], "")

    def test_unsafe_config_is_refused(self):
        store.write_json(store.vault() / "config" / "kabeuchi.json", {"remote_control_args": ["remote-control", "{name}"]})
        with self.assertRaises(ValueError):
            kabeuchi.launch_argv(ART)
        store.write_json(store.vault() / "config" / "kabeuchi.json", {"remote_control_args": ["--bare", "--remote-control"]})
        with self.assertRaises(ValueError):
            kabeuchi.launch_argv(ART)

    def test_material_too_long(self):
        store.write_json(store.vault() / "config" / "kabeuchi.json", {"material_max_chars": 50})
        with self.assertRaises(ValueError):
            kabeuchi.start(ART)


class KabeuchiWebTest(ServerMixin, KabeuchiBase):
    def test_web_start_stop(self):
        self.start_server()
        status, body, _ = self.req("/kabeuchi")
        self.assertEqual(status, 200)
        self.assertIn("確認実行して起動", body)
        self.assertIn("Trusted Devices", body)
        self.assertIn("~/.claude/projects/", body)
        url = "/kabeuchi/" + urllib.parse.quote(ART, safe="")
        status, _, headers = self.req(url + "/start", {})
        self.assertEqual(status, 303)
        self.assertNotIn("err=", headers["Location"])
        self.assertTrue(kabeuchi.is_running(ART))
        status, body, _ = self.req("/kabeuchi")
        self.assertIn("起動中", body)
        self.req(url + "/stop", {})
        self.assertFalse(kabeuchi.is_running(ART))
