"""値付けの目安・公開済み記事とクロスセル・執筆ペース（pricing・published・pace・web_growth）の確認。"""
import unittest
import urllib.parse
from datetime import date, timedelta

from notewriter import kakera, pace, pricing, published, writing
from tests.test_publish import DOC
from tests.test_writing import WritingBase, ART
from tests.webutil import ServerMixin


class PricingTest(WritingBase):
    def test_bands_and_density(self):
        short = "# 題\n\n予告\n\n<!-- ここから有料 -->\n\n" + "あ" * 1000
        r = pricing.suggest(short)
        self.assertEqual((r["low"], r["high"]), (100, 300))  # 3000字以下・密度が低い → 一番下のまま
        dense = ("# 題\n\n予告\n\n<!-- ここから有料 -->\n\n" + "".join(
            f"## 見出し{i}\n\n- 項目 {i}: 2026年{i}月 1万円 https://example.go.jp/{i}\n- 期限 {i}日\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n" + "い" * 300
            for i in range(1, 9)))
        r = pricing.suggest(dense)
        self.assertGreaterEqual(r["score"], 4)
        self.assertEqual((r["low"], r["high"]), (300, 500), r["summary"])  # 3000字以下だが密度で1つ上
        self.assertIn("1つ上", r["summary"])

    def test_without_paywall_uses_whole(self):
        r = pricing.suggest("# 題\n\n" + "う" * 7000)
        self.assertEqual((r["low"], r["high"]), (300, 500))  # 6001〜10000字の帯から密度が低いので1つ下
        self.assertIn("全文", r["summary"])


class PublishedTest(WritingBase):
    def test_crud_and_validation(self):
        x = published.add(title="退去費用の見積書チェック", url="https://note.com/x/n/1", price="300", tags="賃貸、退去")
        self.assertEqual(x["id"], "PB0001")
        self.assertEqual(x["tags"], ["賃貸", "退去"])
        with self.assertRaises(ValueError):
            published.add(title="", url="")
        with self.assertRaises(ValueError):
            published.add(title="x", url="javascript:alert(1)")
        with self.assertRaises(ValueError):
            published.add(title="x", price="-5")
        published.update(x["id"], title="改題", url="", price="0", tags=[], published="2026-01-02")
        self.assertEqual(published.get(x["id"])["title"], "改題")
        published.delete(x["id"])
        self.assertEqual(published.list_published(), [])

    def test_crosssell(self):
        published.add(title="退去費用の見積書チェック", url="https://note.com/a", price="300", tags=["賃貸"], summary="見積書の見方")
        published.add(title="猫の写真の撮り方", url="https://note.com/b", price="0", tags=["写真"])
        published.add(title="自分の記事", url="https://note.com/c", article=ART)
        txt = published.crosssell_text("退去費用の確かめ方", exclude_article=ART)
        self.assertIn("## あわせて読みたい", txt)
        self.assertIn("[退去費用の見積書チェック](https://note.com/a)（300円）", txt)
        self.assertIn("見積書の見方", txt)
        self.assertNotIn("猫", txt)
        self.assertNotIn("自分の記事", txt)
        self.assertEqual(published.crosssell_text("無関係なタイトル"), "")


class PaceTest(WritingBase):
    def test_weekly_counts(self):
        today = date.today()
        published.add(title="公開した", published=(today - timedelta(days=today.weekday() + 7)).isoformat())
        s = pace.summary()
        self.assertEqual(s["weeks"][0]["kakera"], 4)  # setUp で作ったかけら
        self.assertEqual(s["weeks"][1]["published"], 1)
        self.assertIn("4 個のかけら", s["headline"])
        self.assertNotIn("サボ", s["headline"])

    def test_quiet_week_is_gentle(self):
        s = pace.summary(today=date.today() + timedelta(days=30))
        self.assertIn("お休み", s["headline"])


class GrowthWebTest(ServerMixin, WritingBase):
    def test_pages(self):
        self.start_server()
        self.generate()
        status, body, _ = self.req("/published/new", {"title": "テスト後編（朝の話の続き）", "url": "https://note.com/z", "price": "300",
                                                      "tags": "朝", "published": "2026-09-01", "summary": "続き"})
        self.assertEqual(status, 303)
        status, body, _ = self.req("/published")
        self.assertIn("朝の話の続き", body)
        url = "/publish/a/" + urllib.parse.quote(ART, safe="")
        status, body, _ = self.req(url)
        self.assertIn("値付けの目安", body)
        self.assertTrue("あわせて読みたい" in body)
        status, _, headers = self.req(url + "/crosssell", {})
        self.assertEqual(status, 303)
        self.assertIn("朝の話の続き", writing.draft_path(ART).read_text(encoding="utf-8"))
        self.assertEqual(writing.versions(ART)[-1]["source"], "human_edit")
        status, _, headers = self.req(url + "/crosssell", {})
        self.assertIn("err=", headers["Location"], "同じ案内文は二重に入れない")
        status, body, _ = self.req("/")
        self.assertIn("ペース", body)
        status, body, _ = self.req("/pace")
        self.assertIn("ノルマ", body)


if __name__ == "__main__":
    unittest.main()
