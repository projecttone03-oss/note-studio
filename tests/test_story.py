"""つぶやき・ボックス・かけらの移動（boxes）と、ボックスから記事を1本書く下書き（story・web_story）の確認。

ダミーのかけらと偽の claude だけを使う。
"""
import json
import os
import re
import time
import urllib.parse

from notewriter import boxes, kakera, publish, story, writing
from tests.test_writing import WritingBase
from tests.webutil import ServerMixin

BOX = "初めての一人旅"
OTHER = "パチンコ"


def result(sections, gaps=()):
    return json.dumps({"sections": sections, "gaps": list(gaps)}, ensure_ascii=False)


class StoryBase(WritingBase):
    def setUp(self):
        super().setUp()
        boxes.create_box(BOX)
        boxes.create_box(OTHER)
        self.a = boxes.post("駅のホームで切符をなくしたことに気づいた。", BOX)["id"]
        self.b = boxes.post("駅員さんに「改札の横を見てごらん」と言われた。", BOX)["id"]
        self.c = boxes.post("切符はベンチの下に落ちていた。ほっとした。", BOX)["id"]
        self.x = boxes.post("パチンコ屋の音がうるさかった。", OTHER)["id"]

    def sample(self):
        return result(
            [{"heading": "なくした朝", "paragraphs": [{"sentences": [
                {"text": "駅のホームで、切符がないことに気づいた。", "kakera": [self.a]},
                {"text": "あわてて周りを見回した。", "kakera": []},
                {"gap": 1}]}]},
             {"heading": "見つかるまで", "paragraphs": [{"sentences": [
                 {"text": "駅員さんに「改札の横を見てごらん」と言われた。", "kakera": [self.b]},
                 {"text": "切符はベンチの下にあった。", "kakera": [self.c, "K9999"]},
                 {"text": "それは3時のことだった。", "kakera": []}]}]}],
            [{"no": 1, "question": "切符がないと気づいたとき、最初に何をしましたか？", "why": "気づいた直後が分からない", "priority": 1},
             {"no": 2, "question": "旅の行き先はどこでしたか？", "why": "", "priority": 2}])

    def make_story(self, text=None):
        self.set_result(text or self.sample())
        p = story.prepare(BOX)
        return story.run(BOX, p["confirm_token"])


class BoxTest(StoryBase):
    def test_post_and_remember_box(self):
        k = kakera.get_kakera(self.x)
        self.assertEqual((k["article"], k["section"], k["source"]), (OTHER, "", "つぶやき"))
        self.assertIn("五感", k["viewpoints"], "観点はキーワードから自動で付く")
        self.assertEqual(boxes.last_box(), OTHER, "前回選んだボックスを覚える")
        with self.assertRaises(ValueError):
            boxes.post("  ", BOX)
        with self.assertRaises(ValueError):
            boxes.post("x", "ないボックス")
        self.assertEqual([k["id"] for k in boxes.kakera_in(BOX)], [self.c, self.b, self.a], "新しい順")

    def test_move_one_and_many(self):
        kakera.update_kakera(self.a, section="なくした朝")
        self.assertEqual(boxes.move([self.a], OTHER), [self.a])
        k = kakera.get_kakera(self.a)
        self.assertEqual((k["article"], k["section"]), (OTHER, ""), "移すと区間は空に戻す")
        self.assertEqual(boxes.move([self.b, self.c, self.x], OTHER), [self.b, self.c], "同じボックスのものは飛ばす")
        self.assertEqual(boxes.kakera_in(BOX), [])
        boxes.move([self.a], boxes.UNBOXED)
        self.assertEqual(kakera.get_kakera(self.a)["article"], "")
        self.assertIn(boxes.UNBOXED, [b["name"] for b in boxes.list_boxes()])
        with self.assertRaises(ValueError):
            boxes.move([self.a], "ないボックス")
        with self.assertRaises(ValueError):
            boxes.move([], OTHER)

    def test_boxes_lifecycle(self):
        with self.assertRaises(ValueError):
            boxes.create_box(BOX)
        with self.assertRaises(ValueError):
            boxes.delete_box(BOX)
        boxes.create_box("空っぽ")
        boxes.delete_box("空っぽ")
        kakera.create_kakera("古いデータ", "一覧にない記事", "", [])
        boxes.ensure_registered()
        self.assertIn("一覧にない記事", boxes.box_names(), "古いデータのボックスも一覧に出る")


class StoryTest(StoryBase):
    def test_generate_reads_as_one_article(self):
        job = self.make_story()
        self.assertEqual(job["status"], "done", job.get("error"))
        call = self.last_call()
        self.assertIn("切符をなくした", call["stdin"])
        self.assertNotIn("パチンコ屋", call["stdin"], "ほかのボックスのかけらは渡さない")
        self.assertEqual(call["allowed"], "", "ツールなし")
        self.assertNotIn("切符", " ".join(call["argv"]))
        text = writing.draft_path(BOX).read_text(encoding="utf-8")
        self.assertTrue(text.startswith(f"# {BOX}\n\n## なくした朝\n\n駅のホームで、切符がないことに気づいた。あわてて周りを見回した。【足りない①】"), text)
        self.assertIn("## 見つかるまで", text)
        meta = writing.versions(BOX)[-1]
        self.assertEqual(meta["source"], "ai_story")
        self.assertEqual(meta["kakera_ids"], [self.a, self.b, self.c])
        self.assertEqual(kakera.list_provenance()[-1]["kakera_ids"], [self.a, self.b, self.c])
        # 1文ずつの根拠と「足した文」
        bridges = [s["text"] for _, s in story.bridge_sentences(BOX)]
        self.assertEqual(bridges, ["あわてて周りを見回した。", "それは3時のことだった。"])
        sents = [s for b in story.current_blocks(BOX) for s in b.get("sentences") or []]
        by = {s["text"]: s for s in sents}
        self.assertIn("K9999", " ".join(by["切符はベンチの下にあった。"]["flags"]))
        self.assertEqual(by["切符はベンチの下にあった。"]["kakera"], [self.c])
        self.assertIn("3時", " ".join(by["それは3時のことだった。"]["flags"]), "足した文の数字は要確認")
        # 区間（見出し）を自動で付ける
        self.assertEqual(kakera.get_kakera(self.a)["section"], "なくした朝")
        self.assertEqual(kakera.get_kakera(self.c)["section"], "見つかるまで")
        # 質問リスト（大事な順・印の番号と同じ）
        gaps = story.state(BOX)["gaps"]
        self.assertEqual([(g["no"], g["section"]) for g in gaps], [(1, "なくした朝"), (2, "")])
        self.assertEqual(job["bridge_count"], 2)

    def test_answer_and_regenerate_with_human_sentences(self):
        self.make_story()
        k = story.answer(BOX, 1, "かばんの中を全部出した。")
        got = kakera.get_kakera(k["id"])
        self.assertEqual((got["article"], got["section"]), (BOX, "なくした朝"))
        self.assertEqual(story.state(BOX)["gaps"][0]["answered"], k["id"])
        with self.assertRaises(ValueError):
            story.answer(BOX, 99, "x")
        # 本人が1文だけ直す → 足した文の印は残り、直した文は本人の文になる。文体学習の記録は文の単位
        text = writing.draft_path(BOX).read_text(encoding="utf-8")
        writing.save_human_edit(BOX, text.replace("駅のホームで、切符がないことに気づいた。", "ホームで切符がないと気づいた。"))
        b = [x for x in story.current_blocks(BOX) if x.get("sentences")][0]
        self.assertEqual([s["origin"] for s in b["sentences"]][:2], ["human", "ai"])
        self.assertTrue(b["sentences"][1]["bridge"], "直していない足した文は印が残る")
        edit = writing.list_edits()[-1]
        self.assertEqual((edit["ai"], edit["human"]), ("駅のホームで、切符がないことに気づいた。", "ホームで切符がないと気づいた。"))
        # 書き直し: 答えたかけらと、本人の文（H1）を渡す
        hs = story.human_sentences(BOX)
        self.assertEqual(hs, [{"id": "H1", "text": "ホームで切符がないと気づいた。"}])
        job = self.make_story(result([{"heading": "朝", "paragraphs": [{"sentences": [
            {"text": "ホームで切符がないと気づいた。", "kakera": ["H1"]},
            {"text": "かばんの中を全部出した。", "kakera": [k["id"]]}]}]}]))
        self.assertEqual(job["status"], "done", job.get("error"))
        stdin = self.last_call()["stdin"]
        self.assertIn("かばんの中を全部出した", stdin)
        self.assertIn("[H1] ホームで切符がないと気づいた。", stdin)
        self.assertEqual(story.bridge_sentences(BOX), [], "本人の文を根拠にした文は「足した文」ではない")
        self.assertEqual(len(writing.versions(BOX)), 3, "前の版は残る")

    def test_replace_paragraph(self):
        self.make_story()
        blocks = story.current_blocks(BOX)
        i = next(n for n, b in enumerate(blocks) if b["kind"] == "para")
        story.replace_paragraph(BOX, i, "書き直した段落。")
        self.assertIn("書き直した段落。", writing.draft_path(BOX).read_text(encoding="utf-8"))
        n_before = len(story.current_blocks(BOX))
        story.replace_paragraph(BOX, i, "")
        self.assertEqual(len(story.current_blocks(BOX)), n_before - 1, "空にすると段落を消す")
        with self.assertRaises(ValueError):
            story.replace_paragraph(BOX, 0, "見出しは直せない")

    def test_errors_and_safety(self):
        boxes.create_box("空")
        with self.assertRaises(ValueError):
            story.prepare("空")
        os.environ["FAKE_MODE"] = "tools"
        job = self.make_story()
        self.assertEqual(job["status"], "error")
        self.assertEqual(writing.versions(BOX), [])
        os.environ["FAKE_MODE"] = "ok"
        job = self.make_story("書けませんでした")
        self.assertEqual(job["status"], "error")

    def test_publish_check_counts(self):
        self.make_story()
        text = writing.draft_path(BOX).read_text(encoding="utf-8")
        fs = publish.check_boundary(text)
        self.assertTrue(any(f["severity"] == "strong" and "【足りない】" in f["message"] for f in fs))
        self.assertEqual(story.gap_marks(text), 1)

    def test_adopted_style_rules_are_used(self):
        writing.save_style(writing.get_style() + "\n- 一文は40字以内にする\n")
        self.assertIn("一文は40字以内にする", story.prepare(BOX)["prompt"])

    def test_old_section_draft_still_shows(self):
        writing.save_human_edit(BOX, f"# {BOX}\n\n## 朝\n\n前の画面で書いた段落。\n")
        self.assertEqual(story.human_sentences(BOX), [{"id": "H1", "text": "前の画面で書いた段落。"}])
        self.assertEqual(story.bridge_sentences(BOX), [])

    def test_split_sentences(self):
        self.assertEqual(story.split_sentences("朝だ。「おはよう」と言った！【足りない②】次へ"),
                         ["朝だ。", "「おはよう」と言った！", "【足りない②】", "次へ"])


class StoryWebTest(ServerMixin, StoryBase):
    def wait(self, location):
        jid = re.search(r"/story/jobs/(TJ\d+)", location)[1]
        for _ in range(100):
            if story.JOB.get(jid)["status"] != "running":
                return story.JOB.get(jid)
            time.sleep(0.1)
        self.fail("終わりませんでした")

    def test_tsubuyaki_flow(self):
        self.start_server()
        status, body, _ = self.req("/tsubuyaki")
        self.assertEqual(status, 200)
        self.assertIn(f'<option value="{OTHER}" selected>', body, "前回のボックスが選ばれている")
        self.assertIn("投稿", body)
        status, _, headers = self.req("/tsubuyaki", {"box": BOX, "body": "電車の窓から海が見えた。"})
        self.assertEqual(status, 303)
        status, body, _ = self.req(headers["Location"])
        self.assertIn("電車の窓から海が見えた。", body)
        self.assertIn('name="body"', body)
        self.assertNotIn("電車の窓から海が見えた。</textarea>", body, "投稿したら入力欄は空")
        new = boxes.kakera_in(BOX)[0]["id"]
        status, _, _ = self.req("/tsubuyaki/move", {"from": BOX, "to": OTHER, "ids": [new, self.a]})
        self.assertEqual([k["article"] for k in map(kakera.get_kakera, (new, self.a))], [OTHER, OTHER])
        status, _, headers = self.req("/tsubuyaki/box", {"name": "借金"})
        self.assertIn("借金", boxes.box_names())
        status, body, _ = self.req("/")
        self.assertIn('href="/tsubuyaki"', body, "ダッシュボードから1回で行ける")

    def test_story_flow(self):
        self.start_server()
        url = "/story/b/" + urllib.parse.quote(BOX, safe="")
        status, body, _ = self.req("/story")
        self.assertIn(BOX, body)
        status, body, _ = self.req(url)
        self.assertIn("このボックスのかけらで下書きを作る", body)
        self.set_result(self.sample())
        status, body, _ = self.req(url + "/generate", {})
        self.assertIn("渡す全文を見る", body, "初期値は毎回確認")
        token = re.search(r'name="confirm_token" value="([0-9a-f]+)"', body)[1]
        status, _, headers = self.req(url + "/run", {"confirm_token": token})
        self.assertEqual(self.wait(headers["Location"])["status"], "done")
        status, body, _ = self.req(url)
        self.assertIn('class="bridge"', body)
        self.assertIn("【足りない①】", body)
        self.assertIn("切符がないと気づいたとき、最初に何をしましたか？", body)
        self.assertNotIn('class="blk', body, "1段落ごとの箱は使わない")
        status, _, _ = self.req(url + "/gap/1", {"answer": "かばんを探した。"})
        status, body, _ = self.req(url)
        self.assertIn("済み", body)
        status, body, _ = self.req(url + "/bridge")
        self.assertIn("あわてて周りを見回した。", body)
        status, body, _ = self.req("/publish/a/" + urllib.parse.quote(BOX, safe=""))
        self.assertIn("Claude が足した文", body)
        self.assertIn("2文", body)
        # 確認なしの設定
        self.req(url + "/settings", {})
        self.assertFalse(story.confirm_before_send())
        status, _, headers = self.req(url + "/generate", {})
        self.assertIn("/story/jobs/", headers["Location"], "確認なしならすぐ作る")
        self.wait(headers["Location"])
        # 段落を押して直す
        blocks = story.current_blocks(BOX)
        i = next(n for n, b in enumerate(blocks) if b["kind"] == "para")
        status, _, _ = self.req(url + "/para", {"index": str(i), "text": "手で直した段落。"})
        self.assertIn("手で直した段落。", writing.draft_path(BOX).read_text(encoding="utf-8"))
