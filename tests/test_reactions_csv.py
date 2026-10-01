"""反応記録の CSV 取り込み（reactions_csv・web_reactions_csv）の確認。ダミーの CSV だけを使う。"""
from notewriter import reactions, reactions_csv as RC
from tests.test_writing import WritingBase
from tests.webutil import ServerMixin

CSV = ("記事タイトル,公開日,ビュー,スキ,コメント,販売数,その他\n"
       "退去費用の確かめ方,2026/09/01,\"1,234\",12,3,5,x\n"
       "スキマバイトの還付,2026-09-10,50,4,0,1,y\n"
       ",2026-09-10,1,1,1,1,z\n"
       "数が変,2026-09-10,1,たくさん,0,0,z\n")


class CsvTest(WritingBase):
    def test_parse(self):
        r = RC.parse(CSV, "2026-10-01")
        self.assertEqual(len(r["rows"]), 2)
        a = r["rows"][0]
        self.assertEqual((a["title"], a["published"], a["likes"], a["comments"], a["purchases"]),
                         ("退去費用の確かめ方", "2026-09-01", 12, 3, 5))
        self.assertIn("閲覧数: 1,234", a["memo"])
        self.assertEqual([n for n, _ in r["errors"]], [4, 5])
        self.assertIn("その他", r["unknown"])

    def test_sjis_and_missing_title_column(self):
        self.assertIn("退去費用", RC.decode(CSV.encode("cp932")))
        with self.assertRaises(ValueError) as cm:
            RC.parse("名前,数\nx,1\n")
        self.assertIn("columns.title", str(cm.exception))

    def test_import_skips_duplicates(self):
        r = RC.parse(CSV, "2026-10-01")
        self.assertEqual(RC.import_rows(r["rows"]), (2, 0))
        self.assertEqual(RC.import_rows(r["rows"]), (0, 2))
        self.assertEqual(len(reactions.list_reactions()), 2)


class CsvWebTest(ServerMixin, WritingBase):
    def test_flow(self):
        self.start_server()
        status, body, _ = self.req("/reactions")
        self.assertIn("CSV を取り込む", body)
        status, body, _ = self.req("/reactions/import")
        self.assertIn("読み取って確認する", body)
        status, body, _ = self.req("/reactions/import/preview", {"recorded": "2026-10-01"},
                                   files=[("file", "note.csv", CSV.encode("cp932"))])
        self.assertEqual(status, 200)
        self.assertIn("2 件を記録する", body)
        self.assertIn("4行目", body)
        self.assertEqual(reactions.list_reactions(), [], "確認の段階では記録しない")
        status, _, headers = self.req("/reactions/import/run", {"recorded": "2026-10-01", "csv": CSV})
        self.assertEqual(status, 303)
        self.assertEqual(len(reactions.list_reactions()), 2)
