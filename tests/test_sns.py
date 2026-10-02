"""SNS 導線（sns・web_sns）の確認。ダミーデータと偽の claude だけを使う。投稿はしない。"""
import json
import os
import re
import time
import urllib.parse
from datetime import date, timedelta

from notewriter import sns, store, writing
from tests.test_writing import WritingBase, ART
from tests.webutil import ServerMixin


class SnsBase(WritingBase):
    def make_draft(self):
        self.generate()
        text = writing.draft_path(ART).read_text(encoding="utf-8")
        writing.save_human_edit(ART, text.replace("## 夜", "<!-- ここから有料 -->\n\n## 夜") + "\n有料だけの秘密の話。\n")


class SnsTest(SnsBase):
    def test_intent_url_from_config(self):
        self.assertEqual(sns.intent_url("x", "a b&c"), "https://x.com/intent/tweet?text=a%20b%26c")
        self.assertTrue(sns.intent_url("threads", "あ").startswith("https://www.threads.com/intent/post?text=%E3%81%82"))
        store.write_json(store.vault() / "config" / "sns.json",
                         {"platforms": {"x": {"label": "X", "intent_url": "https://twitter.com/intent/tweet?text={text}"}}})
        self.assertTrue(sns.intent_url("x", "a").startswith("https://twitter.com/"))
        store.write_json(store.vault() / "config" / "sns.json",
                         {"platforms": {"x": {"label": "X", "intent_url": "javascript:alert({text})"}}})
        with self.assertRaises(ValueError):
            sns.intent_url("x", "a")

    def test_x_weight(self):
        self.assertEqual(sns.x_weight("abc"), 3)
        self.assertEqual(sns.x_weight("あいう"), 6)
        self.assertEqual(sns.x_weight("見て https://note.com/abc/n/123456789"), 4 + 1 + 23)
        self.assertTrue(sns.length_flags("x", "あ" * 141))
        self.assertFalse(sns.length_flags("x", "あ" * 140))

    def test_promo_ratio(self):
        today = date.today()
        for i in range(9):
            x = sns.add_post("x", "知ってもらう", f"投稿{i}")
            sns.mark_posted(x["id"], today.isoformat())
        p = sns.add_post("x", "直接宣伝", "買ってね")
        sns.mark_posted(p["id"], today.isoformat())
        self.assertFalse(sns.promo_ratio()["over"], "10% ちょうどは警告しない")
        p = sns.add_post("threads", "直接宣伝", "買ってね2")
        sns.mark_posted(p["id"], today.isoformat())
        r = sns.promo_ratio()
        self.assertTrue(r["over"])
        self.assertEqual((r["total"], r["promo"]), (11, 2))
        old = sns.add_post("x", "直接宣伝", "昔の宣伝")
        sns.mark_posted(old["id"], (today - timedelta(days=40)).isoformat())
        self.assertEqual(sns.promo_ratio()["total"], 11, "30日より前は数えない")
        sns.add_post("x", "直接宣伝", "まだ下書き")
        self.assertEqual(sns.promo_ratio()["total"], 11, "下書きは数えない")

    def test_validation(self):
        with self.assertRaises(ValueError):
            sns.add_post("mastodon", "直接宣伝", "x")
        with self.assertRaises(ValueError):
            sns.add_post("x", "宣伝", "x")
        with self.assertRaises(ValueError):
            sns.add_post("x", "直接宣伝", "  ")

    def test_health(self):
        self.assertEqual(sns.health_due(), ["X", "Threads"])
        month = date.today().strftime("%Y-%m")
        sns.save_health(month, "x", {"bookmarks": "12", "profile_clicks": "3", "impressions": "1,200"})
        sns.save_health(month, "x", {"bookmarks": "13", "profile_clicks": "", "impressions": "1300"})
        h = sns.list_health()
        self.assertEqual(len(h), 1)
        self.assertEqual((h[0]["bookmarks"], h[0]["profile_clicks"], h[0]["impressions"]), (13, None, 1300))
        self.assertEqual(sns.health_due(), ["Threads"])
        with self.assertRaises(ValueError):
            sns.save_health("2026/10", "x", {})
        with self.assertRaises(ValueError):
            sns.save_health(month, "x", {"bookmarks": "-1"})

    def test_generate_uses_free_area_only(self):
        self.make_draft()
        self.set_result(json.dumps({"x": "朝の目覚めの話を書きました。", "threads": "母の「もう起きたの」から始まる朝の話。3年かかった。"},
                                   ensure_ascii=False))
        p = sns.prepare(ART, "知ってもらう")
        job = sns.run(ART, "知ってもらう", p["confirm_token"])
        self.assertEqual(job["status"], "done", job.get("error"))
        call = self.last_call()
        self.assertIn("目覚ましより先に", call["stdin"])
        self.assertNotIn("有料だけの秘密の話", call["stdin"], "有料部分は渡さない")
        self.assertNotIn("目覚ましが鳴る前に", call["stdin"], "かけらは渡さない")
        self.assertEqual(call["allowed"], "")
        posts = sns.list_posts("下書き")
        self.assertEqual({x["platform"] for x in posts}, {"x", "threads"})
        th = [x for x in posts if x["platform"] == "threads"][0]
        self.assertIn("3年", " ".join(th["flags"]))
        self.assertTrue(all(x["status"] == "下書き" for x in posts), "投稿はしない")


class SnsWebTest(ServerMixin, SnsBase):
    def test_flow(self):
        self.start_server()
        self.make_draft()
        status, body, _ = self.req("/sns")
        self.assertEqual(status, 200)
        self.assertIn("アプリは投稿しません", body)
        self.assertIn("実機で確かめていません", body)
        self.assertIn("健康診断がまだです", body)
        status, _, headers = self.req("/sns/new", {"platform": "x", "type": "直接宣伝", "text": "記事を書きました <b>"})
        self.assertEqual(status, 303)
        status, body, _ = self.req("/sns")
        self.assertIn("https://x.com/intent/tweet?text=", body)
        self.assertIn('target="_blank"', body)
        self.assertTrue("記事を書きました &lt;b&gt;" in body and "記事を書きました <b>" not in body)
        x = sns.list_posts()[0]
        status, _, _ = self.req(f"/sns/{x['id']}/posted", {"date": date.today().isoformat()})
        self.assertEqual(sns.get_post(x["id"])["status"], "投稿済み")
        status, body, _ = self.req("/sns?show=all")
        self.assertIn("直接宣伝が多めです", body)
        self.set_result(json.dumps({"x": "目覚ましの話", "threads": "目覚ましの話"}, ensure_ascii=False))
        status, body, _ = self.req("/sns/confirm", {"article": ART, "type": "好きになってもらう"})
        token = re.search(r'name="confirm_token" value="([0-9a-f]+)"', body)[1]
        status, _, headers = self.req("/sns/run", {"article": ART, "type": "好きになってもらう", "confirm_token": token})
        jid = re.search(r"/sns/jobs/(XJ\d+)", headers["Location"])[1]
        for _ in range(100):
            if sns.JOB.get(jid)["status"] != "running":
                break
            time.sleep(0.1)
        self.assertEqual(sns.JOB.get(jid)["status"], "done")
        status, _, _ = self.req("/sns/health", {"month": date.today().strftime("%Y-%m"), "platform": "x", "bookmarks": "5"})
        status, body, _ = self.req("/sns/health")
        self.assertIn("ブックマーク数", body)
        status, body, _ = self.req("/publish/a/" + urllib.parse.quote(ART, safe=""))
        self.assertIn("SNS で知らせる", body)
