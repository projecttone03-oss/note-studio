"""段階1→2の一連の流れとチェックポイントの強制を確認する。

実行: python3 -m unittest discover tests
"""
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="notestudio-test-")
os.environ["NS_DATA_DIR"] = TMP  # notestudio を import する前に設定する

from notestudio import db, export, models, web  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


class FlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = db.connect()
        cls.run_id, cls.theme_ids = models.import_research(cls.conn, load("research_sample.json"))

    def test_1_research_import(self):
        self.assertEqual(len(self.theme_ids), 2)
        t = models.theme_detail(self.conn, self.theme_ids[0])
        self.assertEqual(t["status"], "candidate")
        self.assertEqual(len(t["claims"]), 2)
        self.assertEqual(t["score_total"], 78.0)
        # 同じURLの出典は1つにまとめられる
        n_sources = self.conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
        self.assertEqual(n_sources, 2)
        themes = models.list_themes(self.conn, run_id=self.run_id)
        self.assertEqual(themes[0]["id"], self.theme_ids[0], "スコア順に並ぶ")

    def test_2_plan_blocked_until_human_selects(self):
        with self.assertRaises(models.CheckpointError):
            models.import_plan(self.conn, load("plan_sample.json"))
        models.decide_theme(self.conn, self.theme_ids[0], "hold", "あとで")
        with self.assertRaises(models.CheckpointError):
            models.import_plan(self.conn, load("plan_sample.json"))

    def test_3_plan_cycle(self):
        tid = self.theme_ids[0]
        models.decide_theme(self.conn, tid, "select", "対象者が多い")
        p1 = models.import_plan(self.conn, load("plan_sample.json"))
        self.assertEqual(models.get_theme(self.conn, tid)["status"], "planning")

        models.request_plan_revision(self.conn, p1, "タイトルを短く")
        self.assertEqual(models.get_plan(self.conn, p1)["status"], "revision_requested")

        p2 = models.import_plan(self.conn, load("plan_sample.json"))
        self.assertEqual(models.get_plan(self.conn, p1)["status"], "superseded")
        self.assertEqual(models.get_plan(self.conn, p2)["version"], 2)

        with self.assertRaises(models.CheckpointError):
            models.approve_plan(self.conn, p1, "旧版タイトル", 0, 400)
        models.approve_plan(self.conn, p2, "自分で書いたタイトル", 1, 300, "入口価格で")
        self.assertEqual(models.get_theme(self.conn, tid)["status"], "planned")
        plan = models.get_plan(self.conn, p2)
        self.assertEqual((plan["status"], plan["chosen_outline"], plan["final_price"]), ("approved", 1, 300))

        # 採用し直しても企画確定から巻き戻らない
        models.decide_theme(self.conn, tid, "select")
        self.assertEqual(models.get_theme(self.conn, tid)["status"], "planned")

        export.export_all(self.conn)
        tdir = export.theme_dir(models.get_theme(self.conn, tid))
        self.assertTrue((tdir / "theme.md").exists())
        md = (tdir / "plan_v2.md").read_text(encoding="utf-8")
        self.assertIn("自分で書いたタイトル", md)
        self.assertIn("← 採用", md)
        log = models.research_context(self.conn)["decision_log"]
        self.assertTrue(any(d["note"] == "対象者が多い" for d in log))

    def test_4_validation_messages(self):
        data = load("plan_sample.json")
        data["outlines"][0]["sections"] = [{"heading": "x", "paid": True}] * 3
        data["titles"] = data["titles"][:1]
        errors = models.schemas.validate_plan(data)
        self.assertTrue(any("無料セクションと有料セクション" in e for e in errors))
        self.assertTrue(any("titles" in e for e in errors))

    def test_5_book_source(self):
        """書籍はURLなしで、書名・出版社・該当箇所があれば出典として登録できる。"""
        data = load("plan_sample.json")
        data["claims"].append({"claim": "書籍の指摘", "confidence": "medium",
                               "source": {"type": "book", "title": "架空の本", "publisher": "架空社", "location": "p.42"}})
        self.assertEqual(models.schemas.validate_plan(data), [])
        del data["claims"][-1]["source"]["location"]
        self.assertTrue(any("location" in e for e in models.schemas.validate_plan(data)))
        data["claims"][-1]["source"]["location"] = "p.42"
        models.decide_theme(self.conn, self.theme_ids[1], "select")
        data["theme_id"] = self.theme_ids[1]
        plan = models.get_plan(self.conn, models.import_plan(self.conn, data))
        book = [c for c in plan["claims"] if c["source_type"] == "book"][0]
        self.assertEqual(book["url"], "book:架空の本#p.42")
        self.assertEqual(web.source_link(book["url"], book["source_title"]), "『架空の本』p.42")
        self.assertIn("『架空の本』p.42", export.render_plan(plan, models.get_theme(self.conn, self.theme_ids[1])))


class WebTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        conn = db.connect()
        if not models.list_themes(conn):
            models.import_research(conn, load("research_sample.json"))

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def get(self, path):
        with urllib.request.urlopen(self.base + path) as r:
            return r.status, r.read().decode("utf-8")

    def post(self, path, data, origin=None):
        body = urllib.parse.urlencode(data).encode()
        req = urllib.request.Request(self.base + path, data=body, method="POST")
        req.add_header("Origin", origin or self.base)

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None

        opener = urllib.request.build_opener(NoRedirect)
        try:
            return opener.open(req).status
        except urllib.error.HTTPError as e:
            return e.code

    def test_pages(self):
        for path in ["/", "/themes", "/themes?status=candidate", "/runs", "/runs/1", "/themes/1", "/themes/2",
                     "/plans/1", "/plans/2", "/guide"]:
            status, body = self.get(path)
            self.assertEqual(status, 200, path)
            self.assertIn("note studio", body)

    def test_post_requires_same_origin(self):
        self.assertEqual(self.post("/themes/2/decide", {"action": "reject"}, origin="http://evil.example"), 403)
        self.assertEqual(self.post("/themes/2/decide", {"action": "reject", "note": "需要が弱い"}), 303)
        conn = db.connect()
        self.assertEqual(models.get_theme(conn, 2)["status"], "rejected")

    def test_escaping(self):
        conn = db.connect()
        models.update_theme(conn, 2, {"summary": "<script>alert(1)</script>"})
        _, body = self.get("/themes/2")
        self.assertNotIn("<script>alert(1)</script>", body)


if __name__ == "__main__":
    unittest.main()
