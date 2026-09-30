"""下書き画面（web_writing）の確認。ダミーデータと偽の claude だけを使う。

実行: python3 -m unittest discover tests
"""
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from notewriter import kakera, store, web, writing
from tests.test_writing import FAKE, paras


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(_NoRedirect)
ART = "画面テスト記事"


class WritingWebTest(unittest.TestCase):
    ENV = ("NW_DATA_DIR", "NW_CLAUDE_CMD", "FAKE_MODE", "FAKE_OUT", "FAKE_RESULT")

    @classmethod
    def setUpClass(cls):
        cls._old = {k: os.environ.get(k) for k in cls.ENV}
        cls.tmp = Path(tempfile.mkdtemp(prefix="nw-writing-web-"))
        os.environ["NW_DATA_DIR"] = str(cls.tmp / "vault")
        store.init_vault()
        store.write_json(store.vault() / web.NOTICE_FILE, {"acknowledged_at": store.now(),
                                                           "notice_version": web.NOTICE_VERSION})
        (cls.tmp / "calls").mkdir()
        script = cls.tmp / "fake_claude.py"
        script.write_text(FAKE, encoding="utf-8")
        os.environ["NW_CLAUDE_CMD"] = sys.executable + " " + str(script)
        os.environ["FAKE_OUT"] = str(cls.tmp / "calls")
        os.environ["FAKE_MODE"] = "ok"
        cls.result = cls.tmp / "result.txt"
        os.environ["FAKE_RESULT"] = str(cls.result)
        kakera.save_article(ART, "", 1, ["はじまり", "おわり"])
        cls.k1 = kakera.create_kakera("雨が降っていた。<script>alert(1)</script>", ART, "はじまり", ["五感"])["id"]
        cls.httpd = web.make_server("127.0.0.1", 0)
        cls.httpd.quiet = True
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        for k, v in cls._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def get(self, path):
        try:
            with OPENER.open(self.base + path) as r:
                return r.status, r.read().decode("utf-8"), r.headers
        except urllib.error.HTTPError as ex:
            return ex.code, ex.read().decode("utf-8"), ex.headers

    def post(self, path, fields, headers=None):
        data = urllib.parse.urlencode(fields, doseq=True).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data, method="POST",
                                     headers=headers or {"Sec-Fetch-Site": "same-origin"})
        try:
            with OPENER.open(req) as r:
                return r.status, r.read().decode("utf-8"), r.headers
        except urllib.error.HTTPError as ex:
            return ex.code, ex.read().decode("utf-8"), ex.headers

    def wait_job(self, location):
        jid = re.search(r"/drafts/jobs/(WJ\d+)", location)[1]
        for _ in range(100):
            job = writing.get_job(jid)
            if job["status"] != "running":
                return job
            time.sleep(0.1)
        self.fail("ジョブが終わりませんでした")

    def aurl(self, rest=""):
        return "/drafts/" + urllib.parse.quote(ART, safe="") + rest

    def test_full_flow(self):
        status, body, _ = self.get("/drafts")
        self.assertEqual(status, 200)
        self.assertIn(ART, body)

        # 作成: 確認画面 → 実行
        self.result.write_text(paras(("雨が降っていた。", [self.k1]), ("3時に「帰る」と言った。", [self.k1])), encoding="utf-8")
        status, body, _ = self.post(self.aurl("/generate/confirm"), {"section": "はじまり"})
        self.assertEqual(status, 200)
        self.assertIn("Claude に渡す前の確認", body)
        self.assertNotIn("<script>alert(1)</script>", body, "かけらの HTML はエスケープする")
        token = re.search(r'name="confirm_token" value="([0-9a-f]+)"', body)[1]
        status, _, headers = self.post(self.aurl("/generate/run"), {"section": "はじまり", "confirm_token": token})
        self.assertEqual(status, 303)
        job = self.wait_job(headers["Location"])
        self.assertEqual(job["status"], "done", job.get("error"))

        status, body, _ = self.get(self.aurl())
        self.assertEqual(status, 200)
        self.assertIn("雨が降っていた。", body)
        self.assertIn("要確認", body)  # 3時・「帰る」はかけらにない
        self.assertIn("この区間の下書きを作る", body)

        # 人の手直し → 版2、差分
        text = writing.draft_path(ART).read_text(encoding="utf-8")
        status, _, headers = self.post(self.aurl("/save"), {"body": text.replace("雨が降っていた。", "雨だった。"), "note": "短く"})
        self.assertEqual(status, 303)
        self.assertEqual(writing.versions(ART)[-1]["v"], 2)
        status, body, _ = self.get(self.aurl("/versions?a=1&b=2"))
        self.assertIn("雨だった。", body)
        self.assertIn('class="blk del"', body)

        # 改稿: 提案 → 採用
        blocks = writing.current(ART)["blocks"]
        idx = next(i for i, b in enumerate(blocks) if b["kind"] == "para")
        self.result.write_text(json.dumps({"text": "雨が降っていた。", "kakera": [self.k1]}, ensure_ascii=False), encoding="utf-8")
        status, body, _ = self.post(self.aurl("/revise/confirm"), {"block": idx, "instruction": "元に近づけて"})
        token = re.search(r'name="confirm_token" value="([0-9a-f]+)"', body)[1]
        status, _, headers = self.post(self.aurl("/revise/run"), {"block": idx, "instruction": "元に近づけて",
                                                                  "confirm_token": token})
        job = self.wait_job(headers["Location"])
        self.assertEqual(writing.versions(ART)[-1]["v"], 2, "採用するまで本文は変わらない")
        status, body, _ = self.get(f"/drafts/jobs/{job['id']}")
        self.assertIn("この提案を本文に入れる", body)
        status, _, _ = self.post(f"/drafts/jobs/{job['id']}/accept", {})
        self.assertEqual(status, 303)
        self.assertEqual(writing.versions(ART)[-1]["source"], "ai_revise")

        # 戻す
        status, _, _ = self.post(self.aurl("/restore"), {"v": 1})
        self.assertEqual(writing.versions(ART)[-1]["source"], "restore")

    def test_token_mismatch_is_rejected(self):
        status, _, headers = self.post(self.aurl("/generate/run"), {"section": "はじまり", "confirm_token": "0" * 64})
        self.assertEqual(status, 303)
        self.assertIn("err=", headers["Location"])

    def test_cross_site_post_is_forbidden(self):
        status, _, _ = self.post("/style", {"rules": "x"}, headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 403)

    def test_style_save(self):
        status, _, _ = self.post("/style", {"rules": "# 文体ルール集\n- 一文を短く"})
        self.assertEqual(status, 303)
        self.assertIn("一文を短く", writing.get_style())
        status, body, _ = self.get("/style")
        self.assertIn("一文を短く", body)


if __name__ == "__main__":
    unittest.main()
