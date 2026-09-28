"""リサーチ機能の画面と CLI の確認（ダミーデータのみ。本物の Claude / Perplexity は呼ばない）。

実行: python3 -m unittest discover tests
"""
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
HAS_RESEARCH = all(importlib.util.find_spec(m) is not None for m in
                   ("notewriter.research", "notewriter.secrets", "notewriter.kakera", "notewriter.compliance"))
FAKE_KEY = "pplx-TESTKEY" + "abcdef0123456789XYZ"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(_NoRedirect)


@unittest.skipUnless(HAS_RESEARCH, "リサーチのロジック（research.py / secrets.py）がまだありません")
class ResearchWebTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._old_env = {k: os.environ.get(k) for k in ("NW_DATA_DIR", "PERPLEXITY_API_KEY")}
        cls.tmp = tempfile.mkdtemp(prefix="nw-research-web-")
        os.environ["NW_DATA_DIR"] = os.path.join(cls.tmp, "vault")
        os.environ.pop("PERPLEXITY_API_KEY", None)
        from notewriter import research, secrets, store, web
        cls.store, cls.web, cls.R, cls.S = store, web, research, secrets
        store.init_vault()
        store.write_json(store.vault() / web.NOTICE_FILE, {"notice_version": web.NOTICE_VERSION})
        cls.httpd = web.make_server("127.0.0.1", 0)
        cls.httpd.quiet = True
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        for k, v in cls._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def setUp(self):
        self.S.delete_api_key()
        self.set_budget(1000)

    # --- 道具 ---
    def set_budget(self, jpy):
        self.store.write_json(self.store.vault() / "config" / "research.json", {"monthly_budget_jpy": jpy})

    def get(self, path):
        try:
            with OPENER.open(self.base + path) as r:
                return r.status, r.read().decode("utf-8"), r.headers
        except urllib.error.HTTPError as ex:
            return ex.code, ex.read().decode("utf-8"), ex.headers

    def post(self, path, fields=None, data=None, ctype="application/x-www-form-urlencoded"):
        if data is None:
            data = urllib.parse.urlencode(fields or {}, doseq=True).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data, method="POST", headers={"Content-Type": ctype})
        try:
            with OPENER.open(req) as r:
                return r.status, r.read().decode("utf-8"), r.headers
        except urllib.error.HTTPError as ex:
            return ex.code, ex.read().decode("utf-8"), ex.headers

    def follow(self, headers):
        loc = headers.get("Location")
        self.assertTrue(loc and loc.startswith("/"), loc)
        return self.get(loc)

    def confirm(self, **fields):
        base = {"kind": "trend", "provider": "claude", "article": "", "query": "副業の税金"}
        base.update(fields)
        return self.post("/research/confirm", base)

    # --- 入力画面 ---
    def test_input_prefill_from_query(self):
        q = "副業 <b>確定申告</b>"
        status, body, _ = self.get("/research?" + urllib.parse.urlencode({"query": q, "kind": "deep"}))
        self.assertEqual(status, 200)
        self.assertIn("副業 &lt;b&gt;確定申告&lt;/b&gt;</textarea>", body)
        self.assertRegex(body, r'value="deep" checked')
        self.assertIn("ここに書いた文章だけが外に送られます", body)
        self.assertIn('href="/research"', body)  # ナビ
        self.assertRegex(body, r'value="claude" checked')  # 既定 default_provider

    def test_no_api_key_disables_perplexity(self):
        _, body, _ = self.get("/research")
        for p in ("pplx_standard", "pplx_deep"):
            self.assertRegex(body, rf'<input type="radio" name="provider" value="{p}"[^>]*disabled')
        self.assertIn("APIキー未登録", body)
        self.assertIn("/guide#perplexity", body)
        self.S.set_api_key(FAKE_KEY)
        _, body, _ = self.get("/research")
        self.assertNotRegex(body, r'value="pplx_standard"[^>]*disabled')
        self.assertNotIn(FAKE_KEY, body)

    # --- 確認画面 ---
    def test_confirm_shows_full_text_escaped_and_warns_place(self):
        status, body, _ = self.confirm(query="<script>alert(1)</script> 世田谷区の制度")
        self.assertEqual(status, 200)
        pre = re.search(r'<pre class="send">(.*?)</pre>', body, re.S)
        self.assertIsNotNone(pre)
        expected = self.R.build_prompt("trend", "<script>alert(1)</script> 世田谷区の制度")
        self.assertEqual(self.web.html.unescape(pre.group(1)), expected)  # テンプレート込みの全文
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", body)
        self.assertNotIn("<script>alert(1)", body)
        self.assertIn("世田谷区", body)
        self.assertIn("止めるかどうかはあなたが決めます", body)
        self.assertIn('name="confirm_token"', body)
        self.assertIn("書き直す", body)
        self.assertIn("OK。この内容で送る", body)

    def test_confirm_paid_budget_exceeded_disables_button(self):
        self.S.set_api_key(FAKE_KEY)
        self.set_budget(1)
        status, body, _ = self.confirm(provider="pplx_standard")
        self.assertEqual(status, 200)
        self.assertRegex(body, r'<button class="btn warm"[^>]*disabled>OK。この内容で送る（目安')
        self.assertIn("このままでは送れません", body)
        self.assertIn("だいたい", body)
        self.assertNotIn(FAKE_KEY, body)
        self.set_budget(1000)
        _, body, _ = self.confirm(provider="pplx_standard")
        self.assertRegex(body, r'<button class="btn warm" style="width:100%">OK。この内容で送る（目安')

    def test_confirm_paid_without_key_disabled(self):
        _, body, _ = self.confirm(provider="pplx_standard")
        self.assertRegex(body, r"<button[^>]*disabled>OK。")

    def test_rewrite_returns_values(self):
        status, body, _ = self.post("/research", {"kind": "deep", "provider": "claude", "article": "", "query": "書き直す文章"})
        self.assertEqual(status, 200)
        self.assertIn("書き直す文章</textarea>", body)
        self.assertRegex(body, r'value="deep" checked')

    # --- 実行 ---
    def test_run_redirects_to_job(self):
        p = self.R.prepare("trend", "claude", "副業の税金")
        with mock.patch.object(self.R, "start_job", return_value="J0999") as sj:
            status, _, headers = self.post("/research/run", {"kind": "trend", "provider": "claude", "article": "",
                                                             "query": p["query"], "confirm_token": p["confirm_token"]})
        self.assertEqual(status, 303)
        self.assertTrue(headers["Location"].startswith("/research/jobs/J0999"))
        sj.assert_called_once_with("trend", "claude", "副業の税金", "", p["confirm_token"])

    def test_run_confirm_mismatch_is_shown(self):
        status, body, _ = self.post("/research/run", {"kind": "trend", "provider": "claude", "article": "",
                                                      "query": "別の文章", "confirm_token": "x" * 64})
        self.assertEqual(status, 200)
        self.assertIn("確認した内容と違うため、送りませんでした", body)
        self.assertIn("別の文章</textarea>", body)

    def test_limit_job_asks_and_does_not_continue(self):
        from notewriter import claude_runner
        real_start = self.R.start_job
        p = self.R.prepare("trend", "claude", "上限テスト")
        err = claude_runner.ClaudeLimitError("usage limit reached", resets_at="2026-09-29 03:00")
        with mock.patch.object(self.R, "run_claude", side_effect=err), \
                mock.patch.object(self.R.perplexity, "call") as pcall, \
                mock.patch.object(self.R, "start_job", side_effect=real_start) as sj:
            status, _, headers = self.post("/research/run", {"kind": "trend", "provider": "claude", "article": "",
                                                             "query": p["query"], "confirm_token": p["confirm_token"]})
            self.assertEqual(status, 303)
            jid = headers["Location"].split("/research/jobs/")[1].split("?")[0]
            for _ in range(100):
                if self.R.get_job(jid)["status"] != "running":
                    break
                time.sleep(0.05)
            status, body, _ = self.get(f"/research/jobs/{jid}")
            self.assertEqual(status, 200)
            self.assertIn("Claude の利用上限に達しました", body)
            self.assertIn("Perplexity で続けますか", body)
            self.assertIn("2026-09-29 03:00ごろ解除", body)
            self.assertIn('action="/research/confirm"', body)
            self.assertIn('name="provider" value="pplx_standard"', body)
            self.assertIn("Perplexity で続ける（確認画面へ）", body)
            self.assertIn(">待つ</a>", body)
            self.assertNotIn('http-equiv="refresh"', body)
            pcall.assert_not_called()
            self.assertEqual([c.args[1] for c in sj.call_args_list], ["claude"])
        self.assertEqual(self.R.get_job(jid)["status"], "limit")

    def test_running_job_autorefresh(self):
        job = {"id": "J0500", "status": "running", "kind": "trend", "provider": "claude", "provider_label": "Claude",
               "article": "", "query": "q", "created": "2026-09-28T10:00:00"}
        with mock.patch.object(self.R, "get_job", return_value=job):
            _, body, _ = self.get("/research/jobs/J0500")
        self.assertIn('<meta http-equiv="refresh" content="5">', body)
        self.assertIn("調べています（数分かかることがあります）", body)

    # --- APIキー ---
    def test_api_key_never_shown(self):
        status, body, _ = self.get("/settings/api-key")
        self.assertIn("未登録", body)
        self.assertIn('type="password"', body)
        self.assertIn('autocomplete="off"', body)
        buf = io.StringIO()
        self.httpd.quiet = False
        try:
            with mock.patch.object(sys, "stderr", buf):
                status, body, headers = self.post("/settings/api-key", {"key": FAKE_KEY})
                self.assertEqual(status, 303)
                loc = headers["Location"]
                self.assertNotIn(FAKE_KEY, loc)
                self.assertNotIn(FAKE_KEY, body)
                status, body, _ = self.get(loc)
        finally:
            self.httpd.quiet = True
        self.assertTrue(self.S.has_api_key())
        self.assertIn("登録済み", body)
        self.assertNotIn(FAKE_KEY, body)
        self.assertNotIn(FAKE_KEY[-4:], body)
        self.assertNotIn(FAKE_KEY, buf.getvalue())
        self.assertIn("POST /settings", buf.getvalue())
        for path in ("/", "/research", "/research/usage", "/guide"):
            self.assertNotIn(FAKE_KEY, self.get(path)[1])

    def test_exception_message_redacted(self):
        self.S.set_api_key(FAKE_KEY)
        buf = io.StringIO()
        with mock.patch.object(self.R, "prepare", side_effect=ValueError(f"bad key {FAKE_KEY}")), \
                mock.patch.object(sys, "stderr", buf):
            status, body, _ = self.confirm(provider="pplx_standard")
        self.assertNotIn(FAKE_KEY, body)
        self.assertIn("pplx-****", body)
        with mock.patch.object(self.R, "month_usage", side_effect=RuntimeError(f"boom {FAKE_KEY}")), \
                mock.patch.object(sys, "stderr", buf):
            status, body, _ = self.get("/research/usage")
        self.assertEqual(status, 500)
        self.assertNotIn(FAKE_KEY, body)
        with mock.patch.object(self.S, "set_api_key", side_effect=RuntimeError(f"disk {FAKE_KEY}")), \
                mock.patch.object(sys, "stderr", buf):
            status, body, headers = self.post("/settings/api-key", {"key": FAKE_KEY})
        self.assertNotIn(FAKE_KEY, body + str(headers))
        self.assertNotIn(FAKE_KEY, buf.getvalue())

    def test_api_key_delete(self):
        self.S.set_api_key(FAKE_KEY)
        status, _, headers = self.post("/settings/api-key/delete", {})
        self.assertEqual(status, 303)
        self.assertFalse(self.S.has_api_key())

    # --- 資料 ---
    def multipart(self, fields, files):
        b = uuid.uuid4().hex
        out = b""
        for k, v in fields.items():
            out += (f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n").encode("utf-8")
        for name, content in files:
            out += (f"--{b}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"{name}\"\r\n"
                    f"Content-Type: text/markdown\r\n\r\n").encode("utf-8") + content.encode("utf-8") + b"\r\n"
        out += f"--{b}--\r\n".encode()
        return out, f"multipart/form-data; boundary={b}"

    def test_manual_import_and_stale_badge(self):
        old = (date.today() - timedelta(days=400)).isoformat()
        data, ctype = self.multipart({"article": "前編", "date": old}, [
            ("a.md", "# 古い資料\n\n- 項目 [出典](https://example.com/a)\n<script>x</script>\n"),
            ("b.md", "# もう一つ\n\n本文 https://example.org/b\n")])
        status, _, headers = self.post("/research/materials/import", data=data, ctype=ctype)
        self.assertEqual(status, 303, headers)
        status, body, _ = self.follow(headers)
        self.assertIn("2 件取り込みました", body)
        self.assertIn("1年以上前", body)
        self.assertIn("古い資料", body)
        self.assertIn('type="file" name="files"', body)
        mats = self.R.list_materials("前編")
        mid = next(m["id"] for m in mats if m["title"] == "古い資料")
        status, body, _ = self.get(f"/research/materials/{mid}")
        self.assertEqual(status, 200)
        self.assertIn("1年以上前の資料です", body)
        self.assertIn("書き方・背景の参考", body)
        self.assertIn('href="https://example.com/a" target="_blank" rel="noopener noreferrer"', body)
        self.assertNotIn("<script>x", body)
        self.assertIn("&lt;script&gt;x", body)
        _, home, _ = self.get("/")
        self.assertIn("今月のリサーチ費用", home)
        self.assertRegex(home, r"1年以上前の資料 [1-9]")
        status, _, headers = self.post(f"/research/materials/{mid}/delete", {})
        self.assertEqual(status, 303)
        with self.assertRaises(KeyError):
            self.R.get_material(mid)

    def test_import_rejects_non_md(self):
        data, ctype = self.multipart({"article": "", "date": ""}, [("x.txt", "hello")])
        status, _, headers = self.post("/research/materials/import", data=data, ctype=ctype)
        self.assertEqual(status, 303)
        self.assertIn("err=", headers["Location"])

    def test_usage_and_guide(self):
        status, body, _ = self.get("/research/usage")
        self.assertEqual(status, 200)
        self.assertIn("monthly_budget_jpy", body)
        _, guide, _ = self.get("/guide")
        for s in ("console.perplexity.ai", "Buy more credits", "Auto reload", "Generate API Key",
                  "docs.perplexity.ai/docs/getting-started/pricing", "近い名前のボタンを探してください"):
            self.assertIn(s, guide)

    def test_render_md_is_safe(self):
        out = self.web.render_md('# 見出し\n- [x](javascript:alert(1))\n- <img src=x onerror=1>\n[ok](https://a.example/"q)')
        self.assertNotIn("<img", out)
        self.assertNotIn('href="javascript', out)
        self.assertIn("<h3>見出し</h3>", out)
        self.assertNotIn('"q"', out)


@unittest.skipUnless(HAS_RESEARCH, "リサーチのロジックがまだありません")
class ResearchCliTest(unittest.TestCase):
    def run_nw(self, args, stdin="", env_extra=None):
        env = dict(os.environ, NW_DATA_DIR=self.vault, PYTHONPATH=str(ROOT))
        env.pop("PERPLEXITY_API_KEY", None)
        env.update(env_extra or {})
        return subprocess.run([sys.executable, "-m", "notewriter"] + args, input=stdin, capture_output=True,
                              text=True, env=env, cwd=str(ROOT), start_new_session=True, timeout=60)

    def setUp(self):
        self.vault = tempfile.mkdtemp(prefix="nw-research-cli-")
        self.assertEqual(self.run_nw(["init"]).returncode, 0)

    def test_run_needs_terminal(self):
        # 端末がない（start_new_session で /dev/tty も開けない）ので、送らずに止まる
        fake = os.path.join(self.vault, "fake_claude.sh")
        marker = os.path.join(self.vault, "called")
        Path(fake).write_text(f"#!/bin/sh\ntouch {marker}\necho '{{}}'\n")
        os.chmod(fake, 0o755)
        r = self.run_nw(["research", "run", "--kind", "trend", "副業"], env_extra={"NW_CLAUDE_CMD": fake})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("端末", r.stderr)
        r = self.run_nw(["research", "run", "--kind", "trend"], stdin="副業の税金\nyes\n", env_extra={"NW_CLAUDE_CMD": fake})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("外に送る文章", r.stdout)
        self.assertIn("送りませんでした", r.stdout + r.stderr)
        self.assertFalse(os.path.exists(marker))

    def test_import_list_show_usage_key_status(self):
        md = os.path.join(self.vault, "memo.md")
        Path(md).write_text("# メモ\n\nhttps://example.com/x\n", encoding="utf-8")
        r = self.run_nw(["research", "import", md, "--article", "前編", "--date", "2020-01-01"])
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.run_nw(["research", "list"])
        self.assertIn("1年以上前", r.stdout)
        mid = re.search(r"M\d+", r.stdout).group(0)
        r = self.run_nw(["research", "show", mid])
        self.assertIn("https://example.com/x", r.stdout)
        self.assertIn("体験談", r.stdout)
        r = self.run_nw(["research", "usage"])
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.run_nw(["research", "key", "status"])
        self.assertIn("未登録", r.stdout)
        r = self.run_nw(["research", "key", "status"], env_extra={"PERPLEXITY_API_KEY": FAKE_KEY})
        self.assertIn("環境変数", r.stdout)
        self.assertNotIn(FAKE_KEY, r.stdout + r.stderr)

    def test_key_set_is_not_an_argument(self):
        r = self.run_nw(["research", "key", "set", FAKE_KEY])
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn(FAKE_KEY, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
