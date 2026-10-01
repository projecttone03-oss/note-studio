"""かけらの観点タグの候補（kakera_suggest・web_suggest）の確認。"""
from notewriter import kakera, kakera_suggest as KS
from tests.test_writing import WritingBase
from tests.webutil import ServerMixin


class SuggestTest(WritingBase):
    def test_suggest(self):
        got = [s["viewpoint"] for s in KS.suggest("心臓がどきどきして、母に「大丈夫」と言われた。怖かった。")]
        self.assertIn("体の反応", got)
        self.assertIn("セリフ", got)
        self.assertIn("感情", got)
        self.assertNotIn("セリフ", [s["viewpoint"] for s in KS.suggest("「はい」と言った", ["セリフ"])], "付いている観点は出さない")
        self.assertEqual(KS.suggest(""), [])

    def test_add_viewpoint_only_when_asked(self):
        k = kakera.create_kakera("手が震えた。", "", "", [])
        self.assertEqual(kakera.get_kakera(k["id"])["viewpoints"], [], "候補を出しただけでは付けない")
        KS.add_viewpoint(k["id"], "体の反応")
        self.assertEqual(kakera.get_kakera(k["id"])["viewpoints"], ["体の反応"])
        with self.assertRaises(ValueError):
            KS.add_viewpoint(k["id"], "存在しない観点")


class SuggestWebTest(ServerMixin, WritingBase):
    def test_flow(self):
        self.start_server()
        k = kakera.create_kakera("汗が止まらなかった。", "", "", [])
        status, body, _ = self.req(f"/kakera/{k['id']}")
        self.assertIn("観点の候補", body)
        self.assertIn("＋ 体の反応", body)
        status, body, _ = self.req("/kakera/new")
        self.assertIn("data-sug=", body)
        status, _, headers = self.req(f"/kakera-suggest/{k['id']}", {"viewpoint": "体の反応"})
        self.assertEqual(status, 303)
        self.assertEqual(kakera.get_kakera(k["id"])["viewpoints"], ["体の反応"])
