"""リサーチ型記事の下書き（research_writing・web_rdraft）の確認。ダミーの資料と偽の claude だけを使う。"""
import json
import os
import re
import time
import urllib.parse
from datetime import date, timedelta

from notewriter import kakera, research, research_writing as RW, writing
from tests.test_writing import WritingBase
from tests.webutil import ServerMixin

RART = "退去費用の調べ方"


def result(paras, table=None):
    data = {"paragraphs": [{"section": s, "text": t, "materials": m} for s, t, m in paras]}
    if table:
        data["table"] = table
    return json.dumps(data, ensure_ascii=False)


class ResearchDraftBase(WritingBase):
    def setUp(self):
        super().setUp()
        kakera.save_article(RART, "", 1, ["比べる", "手順"])
        self.m1 = research.import_material("a.md", "# ガイドライン\n経過年数で負担割合が下がる。耐用年数は6年。\n",
                                           article=RART, researched_at=date.today().isoformat())["id"]
        old = (date.today() - timedelta(days=400)).isoformat()
        self.m2 = research.import_material("b.md", "# 古い資料\nクロスの張替えは1平米1000円。\n",
                                           article=RART, researched_at=old)["id"]
        self.mo = research.import_material("c.md", "# 別の記事の資料\n関係ない。\n", article="別記事")["id"]

    def run_rdraft(self, text, ids=None):
        self.set_result(text)
        p = RW.prepare(RART, ids)
        return RW.run(RART, ids, p["confirm_token"])


class ResearchDraftTest(ResearchDraftBase):
    def test_uses_only_selected_materials_and_flags(self):
        job = self.run_rdraft(result(
            [("比べる", "耐用年数は6年とされる。", [self.m1]),
             ("手順", "クロスは1平米1000円が目安。", [self.m2]),
             ("手順", "敷金は3か月分が普通。", [self.m1, "M9999"]),
             ("存在しない区間", "[要追加：管理会社への確認]", [])],
            table={"section": "比べる", "markdown": "| 項目 | 内容 |\n|---|---|\n| 耐用年数 | 6年 |", "materials": [self.m1]}))
        self.assertEqual(job["status"], "done", job.get("error"))
        call = self.last_call()
        self.assertIn("耐用年数は6年", call["stdin"])
        self.assertNotIn("関係ない", call["stdin"], "ほかの記事の資料は渡さない")
        self.assertNotIn("目覚まし", call["stdin"], "かけらは渡さない")
        self.assertEqual(call["allowed"], "")
        blocks = writing.current(RART)["blocks"]
        text = writing.draft_path(RART).read_text(encoding="utf-8")
        self.assertTrue(text.startswith(f"# {RART}\n\n## 比べる\n\n| 項目 | 内容 |"), text)
        by = {b["text"][:6]: b for b in blocks if b["kind"] == "para"}
        self.assertEqual(by["耐用年数は6"]["materials"], [self.m1])
        self.assertIn("鮮度切れ", " ".join(by["クロスは1平"]["flags"]))
        f3 = " ".join(by["敷金は3か月"]["flags"])
        self.assertIn("M9999", f3)
        self.assertIn("3か月", f3, "資料にない数字は要確認")
        self.assertIn("[要追加]", " ".join(by["[要追加：管"]["flags"]))
        sec1 = text.split("## 手順")[0]
        self.assertIn("[要追加：管理会社への確認]", sec1, "知らない区間名の段落は最初の区間へ")
        meta = writing.versions(RART)[-1]
        self.assertEqual(meta["source"], "ai_research")
        self.assertEqual(meta["material_ids"], [self.m1, self.m2])

    def test_prepare_reports_stale_and_selection(self):
        p = RW.prepare(RART, [self.m1])
        self.assertEqual(p["material_ids"], [self.m1])
        self.assertEqual(p["stale"], [])
        p = RW.prepare(RART)
        self.assertEqual([s["id"] for s in p["stale"]], [self.m2])

    def test_human_edit_keeps_material_ids(self):
        self.run_rdraft(result([("比べる", "耐用年数は6年とされる。", [self.m1]), ("手順", "まず見積書を見る。", [self.m1])]))
        text = writing.draft_path(RART).read_text(encoding="utf-8")
        writing.save_human_edit(RART, text.replace("耐用年数は6年とされる。", "耐用年数は6年。"))
        p = [b for b in writing.current(RART)["blocks"] if b["kind"] == "para"][0]
        self.assertEqual(p["materials"], [self.m1])

    def test_conflict_held(self):
        p = RW.prepare(RART)
        self.run_rdraft(result([("比べる", "一つ目。", [self.m1])]))
        self.set_result(result([("比べる", "二つ目。", [self.m1])]))
        job = RW.JOB.run(dict(p, job=dict(p["job"], base_version=0)), p["confirm_token"], RW._handle)
        self.assertEqual(job["status"], "conflict")
        self.assertNotIn("二つ目", writing.draft_path(RART).read_text(encoding="utf-8"))
        RW.apply_conflicted(job["id"])
        self.assertIn("二つ目", writing.draft_path(RART).read_text(encoding="utf-8"))

    def test_tools_rejected(self):
        os.environ["FAKE_MODE"] = "tools"
        job = self.run_rdraft(result([("比べる", "x", [self.m1])]))
        self.assertEqual(job["status"], "error")
        self.assertEqual(writing.versions(RART), [])

    def test_no_materials(self):
        kakera.save_article("資料なし", "", 3, ["一"])
        with self.assertRaises(ValueError):
            RW.prepare("資料なし")


class ResearchDraftWebTest(ServerMixin, ResearchDraftBase):
    def test_flow(self):
        self.start_server()
        url = "/rdraft/" + urllib.parse.quote(RART, safe="")
        status, body, _ = self.req(url)
        self.assertEqual(status, 200)
        self.assertIn("鮮度切れ", body)
        self.set_result(result([("比べる", "耐用年数は6年とされる。", [self.m1])]))
        status, body, _ = self.req(url + "/confirm", {"ids": [self.m1, self.m2]})
        self.assertIn("鮮度切れの資料があります", body)
        token = re.search(r'name="confirm_token" value="([0-9a-f]+)"', body)[1]
        status, _, headers = self.req(url + "/run", {"ids": [self.m1, self.m2], "confirm_token": token})
        self.assertEqual(status, 303)
        jid = re.search(r"/rdraft/jobs/(RJ\d+)", headers["Location"])[1]
        for _ in range(100):
            if RW.JOB.get(jid)["status"] != "running":
                break
            time.sleep(0.1)
        self.assertEqual(RW.JOB.get(jid)["status"], "done")
        status, body, _ = self.req("/drafts/" + urllib.parse.quote(RART, safe=""))
        self.assertIn(f'/research/materials/{self.m1}', body)
        self.assertIn("資料から下書きを作る", body)
