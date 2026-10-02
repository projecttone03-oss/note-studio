"""無料/有料の境界チェックと note スマホプレビュー（publish・web_publish）の確認。ダミーの本文だけを使う。"""
import unittest
import urllib.parse

from notewriter import publish, writing
from tests.test_writing import WritingBase, ART
from tests.webutil import ServerMixin

DOC = """# 退去費用の確かめ方

この記事で分かること:
- 原状回復のガイドラインの読み方
- 見積書のチェック手順

<!-- ここから有料 -->

## 原状回復のガイドラインの読み方

本文。

## 見積書のチェック手順

本文。

## 交渉メールの文例

本文。
"""


class BoundaryTest(unittest.TestCase):
    def test_teaser_missing(self):
        fs = publish.check_boundary(DOC)
        msgs = [f["message"] for f in fs if f["severity"] == "warn"]
        self.assertEqual(len(msgs), 1, msgs)
        self.assertIn("交渉メールの文例", msgs[0])

    def test_no_marker_and_multiple(self):
        fs = publish.check_boundary("# 題\n\n本文\n")
        self.assertTrue(any("区切り行がありません" in f["message"] for f in fs))
        fs = publish.check_boundary(DOC + "\n【ここから有料】\n")
        self.assertEqual(fs[0]["severity"], "strong")

    def test_missing_marker_strong(self):
        fs = publish.check_boundary(DOC.replace("本文。", "[要追加：例]", 1))
        self.assertTrue(any(f["severity"] == "strong" and "[要追加]" in f["message"] for f in fs))

    def test_split_and_markers_normalized(self):
        sp = publish.split("無料\n　【ここから有料】 \n有料\n")
        self.assertEqual((sp["free"], sp["paid"], sp["marker_line"]), ("無料", "有料\n", 2))

    def test_preview_html(self):
        html = publish.preview_page(DOC + "\n| 項目 | 金額 |\n|---|---|\n| <b>畳</b> | 3000 |\n", "題", "500")
        self.assertIn("max-width:620px", html)
        self.assertIn('<nav class="toc">', html)
        self.assertIn("ここから先は有料部分です", html)
        self.assertIn("500円", html)
        self.assertIn("<table>", html)
        self.assertIn("&lt;b&gt;畳", html, "本文の HTML はエスケープする")
        self.assertLess(html.index('class="toc"'), html.index("<h2"), "目次は最初の見出しの手前")
        self.assertLess(html.index('class="toc"'), html.index('class="paywall"'), "有料ラインが先なら無料エリアに")
        self.assertNotIn("http://", html.replace("http://www.w3.org", ""))


class PublishWebTest(ServerMixin, WritingBase):
    def test_pages(self):
        self.start_server()
        self.generate()
        text = writing.draft_path(ART).read_text(encoding="utf-8")
        writing.save_human_edit(ART, text.replace("## 夜", "<!-- ここから有料 -->\n\n## 夜"))
        status, body, _ = self.req("/publish")
        self.assertIn(ART, body)
        url = "/publish/a/" + urllib.parse.quote(ART, safe="")
        status, body, _ = self.req(url)
        self.assertEqual(status, 200)
        self.assertIn("無料/有料の境界チェック", body)
        self.assertIn("夜", body)
        status, body, headers = self.req(url + "/preview?price=300")
        self.assertEqual(status, 200)
        self.assertIn("note-body", body)
        self.assertIn("300円", body)
        self.assertNotIn("<nav><a", body, "アプリの画面の枠は付けない")
        status, body, _ = self.req("/publish/paste", {"text": DOC, "act": "check"})
        self.assertIn("交渉メールの文例", body)
        status, body, _ = self.req("/publish/paste", {"text": DOC, "act": "preview", "price": "500"})
        self.assertIn("500円", body)


if __name__ == "__main__":
    unittest.main()
