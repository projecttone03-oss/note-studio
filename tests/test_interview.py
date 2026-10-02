"""インタビューモード（interview・web_interview）の確認。ダミーデータだけを使う。"""
import subprocess
import sys
import urllib.parse

from notewriter import interview, kakera, writing
from tests.test_writing import WritingBase, ART, paras
from tests.webutil import ServerMixin


class InterviewTest(WritingBase):
    def test_questions_from_missing_viewpoints_and_markers(self):
        self.generate(result=paras(("目が覚めた。", [self.k1]), ("[要追加：部屋の様子]", [])))
        qs = interview.all_questions(ART)
        self.assertEqual(qs[0]["kind"], "要追加")
        self.assertIn("部屋の様子", qs[0]["text"])
        self.assertEqual(qs[0]["section"], "朝")
        vps = [(q["section"], q["viewpoint"]) for q in qs if q["kind"] == "空いている観点"]
        self.assertNotIn(("朝", "五感"), vps, "かけらのある観点は聞かない")
        self.assertIn(("朝", "体の反応"), vps)
        self.assertIn(("夜", "五感"), vps)

    def test_answer_saves_kakera_and_moves_on(self):
        q = interview.next_question(ART)
        k = interview.answer(ART, q["key"], "心臓がどきどきした。")
        got = kakera.get_kakera(k["id"])
        self.assertEqual((got["article"], got["section"], got["viewpoints"]), (ART, q["section"], [q["viewpoint"]]))
        self.assertIn("インタビュー", got["tags"])
        self.assertEqual(got["body"], "心臓がどきどきした。")
        self.assertNotEqual(interview.next_question(ART)["key"], q["key"])
        with self.assertRaises(ValueError):
            interview.answer(ART, interview.next_question(ART)["key"], "  ")

    def test_skip_and_reset(self):
        q = interview.next_question(ART)
        interview.skip(ART, q["key"])
        self.assertNotEqual(interview.next_question(ART)["key"], q["key"])
        self.assertEqual(interview.reset_skipped(ART), 1)
        self.assertEqual(interview.next_question(ART)["key"], q["key"])

    def test_marker_question_not_repeated(self):
        self.generate(result=paras(("[要追加：部屋の様子]", [])))
        q = interview.next_question(ART)
        interview.answer(ART, q["key"], "カーテンが閉まっていた。")
        self.assertFalse(any(x["kind"] == "要追加" for x in interview.pending(ART)))

    def test_cli(self):
        import os
        q = interview.next_question(ART)
        r = subprocess.run([sys.executable, "-m", "notewriter", "interview", ART], input="s\n答えた。\n\nq\n",
                           capture_output=True, text=True, env=dict(os.environ))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("スキップしました", r.stdout)
        self.assertIn("として保存しました", r.stdout)
        self.assertTrue(any(k["body"] == "答えた。" for k in kakera.list_kakera()))


class InterviewWebTest(ServerMixin, WritingBase):
    def test_flow(self):
        self.start_server()
        status, body, _ = self.req("/interview")
        self.assertIn(ART, body)
        url = "/interview/" + urllib.parse.quote(ART, safe="")
        status, body, _ = self.req(url)
        self.assertIn("保存して次へ", body)
        self.assertIn("マイク", body)
        q = interview.next_question(ART)
        self.assertIn(q["key"], body)
        status, _, headers = self.req(url + "/answer", {"key": q["key"], "answer": "手が冷たかった。", "viewpoints": ["体の反応", "五感"]})
        self.assertEqual(status, 303)
        k = [k for k in kakera.list_kakera() if k["body"] == "手が冷たかった。"][0]
        self.assertEqual(k["viewpoints"], ["体の反応", "五感"])
        status, _, headers = self.req(url + "/answer", {"key": "vp:x:y", "answer": "a"})
        self.assertIn("err=", headers["Location"])
