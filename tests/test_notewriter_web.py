"""notewriter の Web画面と CLI の確認（ダミーデータのみ）。

実行: python3 -m unittest discover tests
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="notewriter-web-test-")
VAULT = os.path.join(TMP, "vault")
os.environ["NW_DATA_DIR"] = VAULT  # notewriter を import する前に設定する

from notewriter import store, web  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HAS_KAKERA = importlib.util.find_spec("notewriter.kakera") is not None
HAS_COMPLIANCE = importlib.util.find_spec("notewriter.compliance") is not None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(_NoRedirect)


class WebTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        store.init_vault()
        cls.httpd = web.make_server("127.0.0.1", 0)
        cls.httpd.quiet = True
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    # --- 道具 ---
    def get(self, path):
        try:
            with OPENER.open(self.base + path) as r:
                return r.status, r.read().decode("utf-8"), r.headers
        except urllib.error.HTTPError as ex:
            return ex.code, ex.read().decode("utf-8"), ex.headers

    def post(self, path, fields, headers=None):
        data = urllib.parse.urlencode(fields, doseq=True).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data, method="POST", headers=headers or {})
        try:
            with OPENER.open(req) as r:
                return r.status, r.read().decode("utf-8"), r.headers
        except urllib.error.HTTPError as ex:
            return ex.code, ex.read().decode("utf-8"), ex.headers

    def ack(self):
        if not web.notice_acked(store.vault()):
            status, _, headers = self.post("/notice/ack", {"next": "/"})
            self.assertEqual(status, 303)

    # --- テスト ---
    def test_01_notice_first(self):
        (Path(VAULT) / web.NOTICE_FILE).unlink(missing_ok=True)
        status, body, headers = self.get("/kakera")
        self.assertEqual(status, 200)
        self.assertIn("はじめにお読みください", body)
        self.assertIn("5年", body)
        self.assertIn("30日", body)
        self.assertIn("Trusted Devices", body)
        self.assertIn("ダミーデータ", body)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")
        status, _, headers = self.post("/notice/ack", {"next": "/kakera"})
        self.assertEqual(status, 303)
        self.assertTrue(headers["Location"].startswith("/kakera"))
        self.assertTrue((Path(VAULT) / web.NOTICE_FILE).is_file())
        ack = json.loads((Path(VAULT) / web.NOTICE_FILE).read_text(encoding="utf-8"))
        self.assertIn("acknowledged_at", ack)
        # 確認後も使い方ページから読める
        status, body, _ = self.get("/guide")
        self.assertEqual(status, 200)
        self.assertIn("Trusted Devices", body)

    def test_02_cross_origin_post_forbidden(self):
        status, _, _ = self.post("/neta/new", {"body": "x"}, {"Origin": "http://evil.example"})
        self.assertEqual(status, 403)
        status, _, _ = self.post("/neta/new", {"body": "x"}, {"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 403)
        status, _, _ = self.post("/neta/new", {"body": "x"}, {"Origin": "null"})
        self.assertEqual(status, 403)
        status, _, _ = self.get_with_host("evil.example")
        self.assertEqual(status, 400)

    def get_with_host(self, host):
        req = urllib.request.Request(self.base + "/", headers={"Host": host})
        try:
            with OPENER.open(req) as r:
                return r.status, r.read().decode("utf-8"), r.headers
        except urllib.error.HTTPError as ex:
            return ex.code, ex.read().decode("utf-8"), ex.headers

    @unittest.skipUnless(HAS_KAKERA, "kakera.py が未完成")
    def test_03_dashboard_and_kakera(self):
        self.ack()
        status, body, _ = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("ダッシュボード", body)
        # 同一オリジン（Sec-Fetch-Site: same-origin と Origin: null の組み合わせも通る）
        status, _, headers = self.post("/kakera/new", {
            "body": "ダミー: 朝、ドアを叩く音で目が覚めた", "article": "ダミー前編", "section": "朝",
            "viewpoints": ["五感", "セリフ"], "viewpoints_extra": "におい", "status": "未使用", "tags": "ダミー, 朝",
        }, {"Origin": "null", "Sec-Fetch-Site": "same-origin"})
        self.assertEqual(status, 303)
        kid = re.search(r"/kakera/(K\d+)", headers["Location"])[1]
        status, body, _ = self.get("/kakera")
        self.assertIn(kid, body)
        self.assertIn("ドアを叩く音", body)
        status, body, _ = self.get("/kakera?" + urllib.parse.urlencode({"q": "ドア", "viewpoint": "五感"}))
        self.assertIn(kid, body)
        status, body, _ = self.get("/kakera?" + urllib.parse.urlencode({"q": "存在しない語"}))
        self.assertNotIn(f'href="/kakera/{kid}"', body)
        status, body, _ = self.get(f"/kakera/{kid}")
        self.assertEqual(status, 200)
        self.assertIn("におい", body)
        # 記事・充足度
        status, _, _ = self.post("/articles/save", {"mode": "new", "name": "ダミー前編", "series": "ダミー",
                                                    "order": "1", "sections": "朝\n昼"})
        self.assertEqual(status, 303)
        status, body, _ = self.get("/coverage/" + urllib.parse.quote("ダミー前編", safe=""))
        self.assertEqual(status, 200)
        self.assertIn('class="cell zero"', body)
        # ネタ帳 → かけら
        status, _, _ = self.post("/neta/new", {"body": "ダミーのネタ", "tags": ""})
        self.assertEqual(status, 303)
        status, body, _ = self.get("/neta")
        nid = re.search(r'id="(N\d+)"', body)[1]
        status, _, headers = self.post(f"/neta/{nid}/promote", {"article": "ダミー前編", "section": "昼",
                                                               "viewpoints": ["感情"]})
        self.assertEqual(status, 303)
        self.assertRegex(headers["Location"], r"^/kakera/K\d+")

    @unittest.skipUnless(HAS_KAKERA, "kakera.py が未完成")
    def test_04_delete_with_provenance(self):
        from notewriter import kakera
        self.ack()
        k = kakera.create_kakera("ダミー: 来歴付きのかけら", article="ダミー後編")
        kakera.record_provenance("ダミー後編", "第2段落", [k["id"]])
        status, body, _ = self.get(f"/kakera/{k['id']}/delete")
        self.assertEqual(status, 200)
        self.assertIn("このかけらを根拠に生成した段落・記事があります", body)
        self.assertIn("第2段落", body)
        self.assertIn('name="force"', body)
        status, _, headers = self.post(f"/kakera/{k['id']}/delete", {"confirm": "1"})
        self.assertEqual(status, 303)
        self.assertIn("err=", headers["Location"])
        kakera.get_kakera(k["id"])  # まだある
        status, _, _ = self.post(f"/kakera/{k['id']}/delete", {"confirm": "1", "force": "1"})
        self.assertEqual(status, 303)
        with self.assertRaises(KeyError):
            kakera.get_kakera(k["id"])
        # 参照なし: GET では消えず、POST で消える
        k2 = kakera.create_kakera("ダミー: 参照なし")
        status, body, _ = self.get(f"/kakera/{k2['id']}/delete")
        self.assertIn("記録はありません", body)
        kakera.get_kakera(k2["id"])
        self.post(f"/kakera/{k2['id']}/delete", {"confirm": "1"})
        with self.assertRaises(KeyError):
            kakera.get_kakera(k2["id"])

    @unittest.skipUnless(HAS_KAKERA and HAS_COMPLIANCE, "kakera.py / compliance.py が未完成")
    def test_05_check_paste(self):
        from notewriter import compliance
        self.ack()
        rules = compliance.load_rules()
        word = self._banned_word(rules)
        text = f"# ダミー\n<script>alert(1)</script>\n気軽に{word}してください。\n"
        status, body, _ = self.post("/check", {"mode": "paste", "name": "ダミー", "text": text})
        self.assertEqual(status, 200)
        self.assertIn("警告は判断材料です。直すかどうかは人が決めます", body)
        self.assertNotIn("<script>alert(1)</script>", body)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", body)
        self.assertRegex(body, r"<mark[^>]*>" + re.escape(word) + "</mark>")
        self.assertNotIn("自動修正", body)
        # 貼り付けた本文は作業フォルダに保存されない
        for p in Path(VAULT).rglob("*"):
            if p.is_file():
                self.assertNotIn("alert(1)", p.read_text(encoding="utf-8", errors="replace"))

    @unittest.skipUnless(HAS_KAKERA and HAS_COMPLIANCE, "kakera.py / compliance.py が未完成")
    def test_06_check_upload_and_drafts(self):
        from notewriter import compliance
        self.ack()
        word = self._banned_word(compliance.load_rules())
        boundary = "----nwtestboundary"
        parts = []
        for fn, txt in (("b.md", f"後編です。{word}"), ("a.md", "前編です。")):
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{fn}"\r\n'
                         f"Content-Type: text/markdown\r\n\r\n{txt}\r\n")
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="mode"\r\n\r\nupload\r\n--{boundary}--\r\n')
        data = "".join(parts).encode("utf-8")
        req = urllib.request.Request(self.base + "/check", data=data, method="POST",
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with OPENER.open(req) as r:
            body = r.read().decode("utf-8")
        self.assertIn("a.md → b.md", body)
        self.assertRegex(body, r"<mark[^>]*>" + re.escape(word) + "</mark>")
        drafts = Path(VAULT) / "drafts"
        drafts.mkdir(exist_ok=True)
        (drafts / "01_zenpen.md").write_text(f"{word}\n", encoding="utf-8")
        status, body, _ = self.get("/check")
        self.assertIn("01_zenpen.md", body)
        status, body, _ = self.post("/check", {"mode": "drafts", "drafts": ["01_zenpen.md"]})
        self.assertEqual(status, 200)
        self.assertRegex(body, r"<mark[^>]*>" + re.escape(word) + "</mark>")
        status, _, headers = self.post("/check", {"mode": "drafts", "drafts": ["../vault/.nw-vault"]})
        self.assertEqual(status, 303)
        self.assertIn("err=", headers["Location"])

    @staticmethod
    def _banned_word(rules):
        """ルールから禁句を1つ取り出す（なければ SPEC の例「アドバイス」）。"""
        def walk(v, key=""):
            if isinstance(v, dict):
                for k, x in v.items():
                    if k.startswith("_"):
                        continue
                    yield from walk(x, k)
            elif isinstance(v, list):
                for x in v:
                    yield from walk(x, key)
            elif isinstance(v, str) and key in ("words", "word", "banned", "terms", "term") and re.fullmatch(r"[^\W\d_]{2,}", v):
                yield v
        for w in walk(rules):
            return w
        return "アドバイス"

    def test_07_vault_missing(self):
        old = os.environ["NW_DATA_DIR"]
        os.environ["NW_DATA_DIR"] = os.path.join(TMP, "not-mounted")
        try:
            os.makedirs(os.environ["NW_DATA_DIR"], exist_ok=True)
            for path in ("/", "/kakera", "/check", "/guide"):
                status, body, _ = self.get(path)
                self.assertEqual(status, 503)
                self.assertIn("作業フォルダが使えません", body)
                self.assertIn("./nw init", body)
                self.assertIn("gocryptfs", body)
            status, _, _ = self.post("/neta/new", {"body": "書き込まれてはいけない"})
            self.assertEqual(status, 503)
            self.assertEqual(os.listdir(os.environ["NW_DATA_DIR"]), [])
        finally:
            os.environ["NW_DATA_DIR"] = old


class HostTest(unittest.TestCase):
    def test_refuse_any_host(self):
        for h in ("0.0.0.0", "::", "[::]", "", "8.8.8.8", "example.com"):
            with self.assertRaises(web.HostRefused, msg=h):
                web.serve(h, 0)
        for h in ("127.0.0.1", "localhost", "100.101.102.103", "::1", "fd7a:115c:a1e0::1"):
            self.assertTrue(web.check_host(h))

    def test_mark_escape(self):
        html_ = web.mark_line("<b>相談</b>&", [(3, 5, "禁句", "warn")])
        self.assertEqual(html_, '&lt;b&gt;<mark class="warn" title="禁句">相談</mark>&lt;/b&gt;&amp;')

    def test_multipart_parser(self):
        b = "XyZ"
        body = (f'--{b}\r\nContent-Disposition: form-data; name="mode"\r\n\r\nupload\r\n'
                f'--{b}\r\nContent-Disposition: form-data; name="files"; filename="前編.md"\r\n'
                f"Content-Type: text/markdown\r\n\r\n本文\r\n2行目\r\n--{b}--\r\n").encode("utf-8")
        form = web.parse_form(f"multipart/form-data; boundary={b}", body)
        self.assertEqual(form.get("mode"), "upload")
        self.assertEqual(form.files[0][1], "前編.md")
        self.assertEqual(form.files[0][2].decode("utf-8"), "本文\r\n2行目")


class CliTest(unittest.TestCase):
    def run_nw(self, *args, data_dir=None, stdin=""):
        env = dict(os.environ, NW_DATA_DIR=data_dir or os.path.join(TMP, "cli-vault"))
        return subprocess.run([str(ROOT / "nw"), *args], input=stdin, capture_output=True, text=True, env=env)

    def test_vault_error_exit2(self):
        r = self.run_nw("kakera", "list", data_dir=os.path.join(TMP, "nowhere"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("./nw init", r.stderr)

    def test_serve_refuses_any(self):
        r = self.run_nw("serve", "--host", "0.0.0.0")
        self.assertEqual(r.returncode, 2)
        self.assertIn("Tailscale", r.stderr)

    @unittest.skipUnless(HAS_KAKERA, "kakera.py が未完成")
    def test_kakera_rm_with_provenance(self):
        self.assertEqual(self.run_nw("init").returncode, 0)
        r = self.run_nw("kakera", "add", "--article", "ダミー", "--viewpoints", "五感,セリフ", stdin="ダミー本文\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        kid = re.search(r"K\d+", r.stdout)[0]
        self.assertIn(kid, self.run_nw("kakera", "search", "--viewpoint", "セリフ").stdout)
        env = dict(os.environ, NW_DATA_DIR=os.path.join(TMP, "cli-vault"), PYTHONPATH=str(ROOT))
        subprocess.run([sys.executable, "-c", f"from notewriter import kakera; kakera.record_provenance('ダミー', '段落1', ['{kid}'])"],
                       env=env, check=True)
        r = self.run_nw("kakera", "rm", kid)
        self.assertEqual(r.returncode, 1)
        self.assertIn("段落1", r.stdout)
        self.assertEqual(self.run_nw("kakera", "show", kid).returncode, 0)
        self.assertEqual(self.run_nw("kakera", "rm", kid, "--force").returncode, 0)
        self.assertEqual(self.run_nw("kakera", "show", kid).returncode, 1)

    @unittest.skipUnless(HAS_COMPLIANCE, "compliance.py が未完成")
    def test_check_exit_codes(self):
        from notewriter import compliance
        word = WebTest._banned_word(compliance.load_rules())
        d = Path(TMP) / "cli-check"
        d.mkdir(exist_ok=True)
        bad, good = d / "bad.md", d / "good.md"
        bad.write_text(f"{word}です\n", encoding="utf-8")
        good.write_text("今日は晴れ。\n", encoding="utf-8")
        nowhere = os.path.join(TMP, "nowhere")  # 作業フォルダなしでも動く
        r = self.run_nw("check", str(bad), "--json", data_dir=nowhere)
        self.assertEqual(r.returncode, 1, r.stderr)
        out = json.loads(r.stdout)
        self.assertTrue(out["findings"])
        self.assertEqual(bad.read_text(encoding="utf-8"), f"{word}です\n")  # 本文は変更しない
        r = self.run_nw("check", str(good), data_dir=nowhere)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = self.run_nw("rules", data_dir=nowhere)
        self.assertIn(r.returncode, (0, 1))
        self.assertIn("ルールファイル", r.stdout)


if __name__ == "__main__":
    unittest.main()


class LayoutTest(unittest.TestCase):
    """画面の枠（メニューのまとまり・スマホの下のタブ・メニュー画面）。"""

    def test_grouped_nav_and_tabbar(self):
        from notewriter import web
        html = web.layout("t", "<p>本文</p>", active="drafts")
        for g in ("集める", "調べる", "書く", "届ける"):
            self.assertIn(g, html)
        self.assertIn('class="tabbar"', html)
        self.assertIn('href="/menu"', html)
        self.assertIn('<a href="/story" class="on">', html, "以前の下書き画面は新しい「下書き」を光らせる")
        keys = [k for k, _, _ in web.NAV]
        self.assertEqual(len(keys), len(set(keys)), "メニューに同じ画面が2回出ない")
        menu = web.page_menu()
        for k, h, l in web.NAV:
            self.assertIn(f'href="{h}"', menu)
        self.assertNotIn('class="tabbar"', web.layout("t", "x", nav=False))
