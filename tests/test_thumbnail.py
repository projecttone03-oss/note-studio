"""サムネイル（thumbnail・web_thumb）の確認。PNG は Chromium がある環境だけで確かめる。"""
import glob
import os
import unittest
import urllib.parse

from notewriter import store, thumbnail as T
from tests.test_writing import WritingBase, ART
from tests.webutil import ServerMixin

CHROME = (glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome") or [""])[0]


class ThumbUnitTest(unittest.TestCase):
    def test_keyword(self):
        self.assertEqual(T.keyword("【保存版】退去費用の確かめ方"), "退去費用")
        self.assertEqual(T.keyword("「原状回復」の読み方【2026年版】"), "原状回復")
        self.assertEqual(T.keyword("スキマバイトの還付申告ガイド"), "還付申告ガイド")
        self.assertEqual(T.keyword("原状回復ガイドラインの読み方"), "原状回復ガイドライン")

    def test_wrap(self):
        lines = T.wrap("あいうえおかきくけこ、さしすせそ", 5, 5)
        self.assertTrue(all(T._width(l) <= 6 for l in lines))
        self.assertFalse(any(l.startswith("、") for l in lines))
        self.assertTrue(T.wrap("あ" * 100, 5, 2)[-1].endswith("…"))


class ThumbTest(WritingBase):
    ENV = WritingBase.ENV + ("NW_CHROME",)

    def test_svg_and_html(self):
        for t in T.config()["templates"]:
            s = T.svg("退去費用の<確かめ方>と見積書のチェック", t)
            self.assertIn('width="1280" height="670"', s)
            self.assertIn("&lt;確かめ方&gt;", s)
            self.assertNotIn("<確かめ方>", s)
            self.assertNotIn("http://", s.replace("http://www.w3.org/2000/svg", ""))
        self.assertEqual(len(T.config()["templates"]), 3)

    def test_generate_without_png_engine(self):
        os.environ["NW_CHROME"] = "/nonexistent"
        out = T.generate(ART, "題", ["center", "band"], png=True)
        self.assertEqual([o["png"] for o in out], ["", ""])
        self.assertEqual([f["name"] for f in T.list_files(ART)], ["center", "band"])
        with self.assertRaises(KeyError):
            T.read_file(ART, "../x.svg")
        with self.assertRaises(ValueError):
            T.generate(ART, "題", ["nope"])

    @unittest.skipUnless(CHROME, "Chromium がない環境")
    def test_png_with_chrome(self):
        os.environ["NW_CHROME"] = CHROME
        out = T.generate(ART, "スキマバイトの還付申告", ["split"])
        self.assertEqual(out[0]["png"], "split.png")
        data, ctype = T.read_file(ART, "split.png")
        self.assertEqual(ctype, "image/png")
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        import struct
        w, h = struct.unpack(">II", data[16:24])
        self.assertEqual((w, h), (1280, 670))

    def test_background_provider_hook(self):
        T.BACKGROUND_PROVIDERS["dummy"] = lambda title, cfg: b"\x89PNG fake"
        try:
            store.write_json(store.vault() / "config" / "thumbnails.json", {"background_provider": "dummy"})
            self.assertIn("data:image/png;base64,", T.svg("題", T.template("center")))
        finally:
            T.BACKGROUND_PROVIDERS.pop("dummy", None)


class ThumbWebTest(ServerMixin, WritingBase):
    ENV = WritingBase.ENV + ("NW_CHROME",)

    def test_flow(self):
        os.environ["NW_CHROME"] = "/nonexistent"
        self.start_server()
        url = "/thumbnails/a/" + urllib.parse.quote(ART, safe="")
        status, body, _ = self.req(url)
        self.assertEqual(status, 200)
        self.assertIn("SVG と HTML だけ", body)
        status, _, headers = self.req(url + "/generate", {"title": "朝の話", "kw": "朝", "tpl": ["center"]})
        self.assertEqual(status, 303)
        status, body, _ = self.req(url)
        self.assertIn("data:image/svg+xml;base64,", body)
        status, body, headers = self.req(url + "/file/center.svg")
        self.assertEqual(headers["Content-Type"], "image/svg+xml")
        self.assertIn("attachment", headers["Content-Disposition"])
