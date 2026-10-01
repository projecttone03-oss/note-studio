"""文体ルールの候補（style_learn・web_style）の確認。ダミーデータと偽の claude だけを使う。"""
import json
import os
import re
import time
import unittest
import urllib.parse

from notewriter import store, style_learn, web, writing
from tests.test_writing import WritingBase, ART
from tests.webutil import ServerMixin


def cands(*rules):
    return json.dumps({"candidates": [{"rule": r, "reason": "語尾をそろえていた", "pairs": [1]} for r in rules]},
                      ensure_ascii=False)


class StyleLearnTest(WritingBase):
    def make_edit(self):
        self.generate()
        text = writing.draft_path(ART).read_text(encoding="utf-8")
        writing.save_human_edit(ART, text.replace("目覚ましより先に目が覚めた。外はまだ暗い。", "目覚ましより早く目が覚めた。外は暗かった。"))

    def run_suggest(self, result):
        self.set_result(result)
        p = style_learn.prepare_from_edits()
        return style_learn.run_from_edits(p["confirm_token"])

    def test_no_edits_is_error(self):
        with self.assertRaises(ValueError):
            style_learn.prepare_from_edits()

    def test_candidates_are_not_applied_until_adopted(self):
        self.make_edit()
        before = writing.get_style()
        job = self.run_suggest(cands("文末を「〜た。」でそろえる", "一文は40字以内にする"))
        self.assertEqual(job["status"], "done", job.get("error"))
        call = self.last_call()
        self.assertIn("目覚ましより早く", call["stdin"], "直しの組を渡す")
        self.assertEqual(call["allowed"], "")
        self.assertNotIn("目覚まし", " ".join(call["argv"]))
        self.assertEqual(writing.get_style(), before, "候補を出しただけではルール集は変わらない")
        items = style_learn.list_candidates("未検討")
        self.assertEqual(len(items), 2)
        c = items[-1]
        style_learn.adopt(c["id"])
        self.assertIn("- " + c["rule"], writing.get_style())
        with self.assertRaises(ValueError):
            style_learn.adopt(c["id"])
        other = style_learn.list_candidates("未検討")[0]
        style_learn.reject(other["id"])
        self.assertEqual(style_learn.get_candidate(other["id"])["status"], "却下")
        self.assertNotIn(other["rule"], writing.get_style())

    def test_duplicates_and_existing_rules_are_skipped(self):
        self.make_edit()
        writing.save_style(writing.get_style() + "\n- 一文は40字以内にする\n")
        job = self.run_suggest(cands("一文は40字以内にする", "体言止めを1段落1回まで使う", "体言止めを1段落1回まで使う"))
        self.assertEqual(job["added"].__len__(), 1)

    def test_long_copy_is_flagged_and_blocked(self):
        self.make_edit()
        long_copy = "目覚ましより早く目が覚めた。外は暗かった。のように書く"
        self.run_suggest(cands(long_copy))
        c = style_learn.list_candidates("未検討")[0]
        self.assertTrue(c["flags"], "材料の文がそのまま長く入っていたら印")
        with self.assertRaises(ValueError):
            style_learn.adopt(c["id"])
        style_learn.adopt(c["id"], "過去の出来事は過去形で短く切る")
        self.assertIn("過去の出来事は過去形で短く切る", writing.get_style())

    def test_tool_session_rejected(self):
        self.make_edit()
        os.environ["FAKE_MODE"] = "tools"
        job = self.run_suggest(cands("何か"))
        self.assertEqual(job["status"], "error")
        self.assertEqual(style_learn.list_candidates(), [])

    def test_longest_common(self):
        from notewriter import jobs
        self.assertEqual(jobs.longest_common("あいうえおかきく", "xxうえおかyy"), "うえおか")
        self.assertEqual(jobs.longest_common("ＸＹＺ", "abcd"), "")
        self.assertEqual(jobs.longest_common("ABC d", "zABCdz"), "ABCd")


class StyleWebTest(ServerMixin, WritingBase):
    def setUp(self):
        super().setUp()
        self.start_server()

    def test_flow(self):
        self.generate()
        text = writing.draft_path(ART).read_text(encoding="utf-8")
        writing.save_human_edit(ART, text.replace("外はまだ暗い。", "外は暗かった。"))
        self.set_result(cands("語尾は過去形にそろえる"))
        status, body, _ = self.req("/style/candidates")
        self.assertEqual(status, 200)
        status, body, _ = self.req("/style/candidates/confirm", {})
        self.assertIn("渡す全文を見る", body)
        token = re.search(r'name="confirm_token" value="([0-9a-f]+)"', body)[1]
        status, _, headers = self.req("/style/candidates/run", {"confirm_token": token})
        self.assertEqual(status, 303)
        jid = re.search(r"/style/jobs/(SJ\d+)", headers["Location"])[1]
        for _ in range(100):
            if style_learn.JOB.get(jid)["status"] != "running":
                break
            time.sleep(0.1)
        status, body, _ = self.req(f"/style/jobs/{jid}")
        self.assertIn("候補を 1 件追加", body)
        c = style_learn.list_candidates("未検討")[0]
        status, _, _ = self.req(f"/style/candidates/{c['id']}/adopt", {"rule": "語尾は過去形にそろえる（会話文は除く）"})
        self.assertEqual(status, 303)
        self.assertIn("会話文は除く", writing.get_style())
        status, body, _ = self.req("/style")
        self.assertIn("直しからルールの候補を出す", body)


if __name__ == "__main__":
    unittest.main()
