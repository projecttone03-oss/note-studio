"""notewriter の下書き作成・改稿（writing）を確認する。

本物の Claude は呼ばない。NW_CLAUDE_CMD にテスト内で作った偽の claude（stdin を保存し、stream-json を出す Python）を指定する。
実行: python3 -m unittest discover tests
"""
import json
import os
import shutil
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from notewriter import kakera, store, writing

FAKE = textwrap.dedent('''
    import json, os, sys
    out = os.environ["FAKE_OUT"]
    mode = os.environ.get("FAKE_MODE", "ok")
    prompt = sys.stdin.read()
    n = len([p for p in os.listdir(out) if p.startswith("call-")]) + 1
    with open(os.path.join(out, "call-%03d.json" % n), "w", encoding="utf-8") as f:
        json.dump({"argv": sys.argv[1:], "stdin": prompt, "allowed": os.environ.get("NW_ALLOWED_TOOLS")}, f,
                  ensure_ascii=False)
    def emit(obj):
        print(json.dumps(obj, ensure_ascii=False), flush=True)
    tools = ["Read"] if mode == "tools" else []
    emit({"type": "system", "subtype": "init", "tools": tools, "mcp_servers": [], "permissionMode": "dontAsk",
          "model": "claude-fake-writing"})
    if mode == "limit":
        emit({"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "resetsAt": 1900000000}})
        sys.exit(0)
    with open(os.environ["FAKE_RESULT"], encoding="utf-8") as f:
        text = f.read()
    emit({"type": "result", "subtype": "success", "is_error": False, "result": text, "duration_ms": 10})
''')

ART = "テスト前編"


def paras(*items):
    return "```json\n" + json.dumps({"paragraphs": [{"text": t, "kakera": list(k)} for t, k in items]},
                                    ensure_ascii=False) + "\n```"


class WritingBase(unittest.TestCase):
    ENV = ("NW_DATA_DIR", "NW_CLAUDE_CMD", "FAKE_MODE", "FAKE_OUT", "FAKE_RESULT")

    def setUp(self):
        self._old = {k: os.environ.get(k) for k in self.ENV}
        self.tmp = Path(tempfile.mkdtemp(prefix="nw-writing-"))
        os.environ["NW_DATA_DIR"] = str(self.tmp / "vault")
        store.init_vault()
        self.calls = self.tmp / "calls"
        self.calls.mkdir()
        script = self.tmp / "fake_claude.py"
        script.write_text(FAKE, encoding="utf-8")
        os.environ["NW_CLAUDE_CMD"] = sys.executable + " " + str(script)
        os.environ["FAKE_OUT"] = str(self.calls)
        os.environ["FAKE_MODE"] = "ok"
        self.result_file = self.tmp / "result.txt"
        os.environ["FAKE_RESULT"] = str(self.result_file)
        # ダミーの記事とかけら（実データではない）
        kakera.save_article(ART, "テスト連載", 1, ["朝", "夜"])
        self.k1 = kakera.create_kakera("目覚ましが鳴る前に目が覚めた。窓の外はまだ暗かった。", ART, "朝", ["五感"])["id"]
        self.k2 = kakera.create_kakera("母が「もう起きたの」と言った。", ART, "朝", ["セリフ"])["id"]
        self.k_hold = kakera.create_kakera("保留中の秘密メモ", ART, "朝", ["感情"], status="保留")["id"]
        self.k_night = kakera.create_kakera("夜の区間だけの話", ART, "夜", ["出来事"])["id"]

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def set_result(self, text):
        self.result_file.write_text(text, encoding="utf-8")

    def last_call(self):
        calls = sorted(self.calls.glob("call-*.json"))
        return json.loads(calls[-1].read_text(encoding="utf-8")) if calls else None

    def generate(self, section="朝", result=None):
        self.set_result(result or paras(("目覚ましより先に目が覚めた。外はまだ暗い。", [self.k1]),
                                        ("母が「もう起きたの」と声をかけた。", [self.k2])))
        p = writing.prepare_generate(ART, section)
        return writing.run_job(p, p["confirm_token"])


class GenerateTest(WritingBase):
    def test_generate_uses_only_section_kakera_without_tools(self):
        job = self.generate()
        self.assertEqual(job["status"], "done", job.get("error"))
        call = self.last_call()
        self.assertIn("目覚ましが鳴る前に", call["stdin"])
        self.assertNotIn("保留中の秘密メモ", call["stdin"], "保留のかけらは渡さない")
        self.assertNotIn("夜の区間だけの話", call["stdin"], "ほかの区間のかけらは渡さない")
        self.assertEqual(call["allowed"], "", "ツールは1つも許可しない")
        argv = call["argv"]
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertIn("--disallowedTools", argv)
        self.assertNotIn("目覚まし", " ".join(argv), "かけらはコマンドライン引数に出さない")
        text = writing.draft_path(ART).read_text(encoding="utf-8")
        self.assertTrue(text.startswith(f"# {ART}\n\n## 朝\n\n目覚ましより先に"))
        self.assertIn("## 夜", text)
        meta = writing.versions(ART)[-1]
        self.assertEqual((meta["v"], meta["source"]), (1, "ai_generate"))
        prov = kakera.list_provenance()[-1]
        self.assertEqual(prov["kakera_ids"], [self.k1, self.k2])
        self.assertEqual(meta["provenance_id"], prov["id"])

    def test_flags_unknown_ids_and_unsupported_terms(self):
        job = self.generate(result=paras(("目が覚めたのは3時だった。「おはよう」と言われた。", [self.k1, "K9999"]),
                                         ("[要追加：部屋の様子]", []),
                                         ("根拠のない一文。", [])))
        self.assertEqual(job["status"], "done", job.get("error"))
        blocks = [b for b in writing.current(ART)["blocks"] if b["kind"] == "para"]
        f0 = " ".join(blocks[0]["flags"])
        self.assertIn("K9999", f0)
        self.assertIn("3時", f0)
        self.assertIn("「おはよう」", f0)
        self.assertEqual(blocks[0]["kakera"], [self.k1])
        self.assertIn("[要追加]", " ".join(blocks[1]["flags"]))
        self.assertNotIn("根拠のかけらIDがありません", " ".join(blocks[1]["flags"]))
        self.assertIn("根拠のかけらIDがありません", " ".join(blocks[2]["flags"]))
        self.assertEqual(kakera.list_provenance()[-1]["kakera_ids"], [self.k1], "存在しないIDは来歴に入れない")

    def test_confirm_token_changes_when_kakera_change(self):
        p = writing.prepare_generate(ART, "朝")
        kakera.update_kakera(self.k1, body="書き換えた")
        p2 = writing.prepare_generate(ART, "朝")
        with self.assertRaises(writing.ConfirmMismatch):
            writing.run_job(p2, p["confirm_token"])

    def test_tool_session_is_rejected_and_nothing_written(self):
        os.environ["FAKE_MODE"] = "tools"
        job = self.generate()
        self.assertEqual(job["status"], "error")
        self.assertIn("安全のため", job["error"])
        self.assertEqual(writing.versions(ART), [])
        self.assertFalse(writing.draft_path(ART).exists())

    def test_limit(self):
        os.environ["FAKE_MODE"] = "limit"
        job = self.generate()
        self.assertEqual(job["status"], "limit")
        self.assertEqual(writing.versions(ART), [])

    def test_errors_without_material(self):
        with self.assertRaises(ValueError):
            writing.prepare_generate(ART, "存在しない区間")
        kakera.save_article("空の記事", "", 2, ["一"])
        with self.assertRaises(ValueError):
            writing.prepare_generate("空の記事", "一")

    def test_broken_json_is_error(self):
        job = self.generate(result="すみません、書けませんでした。")
        self.assertEqual(job["status"], "error")
        self.assertEqual(writing.versions(ART), [])


class EditAndVersionTest(WritingBase):
    def test_human_edit_records_style_pair_and_keeps_citation(self):
        self.generate()
        text = writing.draft_path(ART).read_text(encoding="utf-8")
        edited = text.replace("目覚ましより先に目が覚めた。外はまだ暗い。", "目覚ましより早く目が覚めた。外は暗かった。")
        meta = writing.save_human_edit(ART, edited, "語尾を直した")
        self.assertEqual((meta["v"], meta["source"]), (2, "human_edit"))
        b = [x for x in writing.current(ART)["blocks"] if x["kind"] == "para"][0]
        self.assertEqual((b["origin"], b["kakera"]), ("human", [self.k1]))
        edits = writing.list_edits()
        self.assertEqual(len(edits), 1)
        self.assertEqual(edits[0]["section"], "朝")
        self.assertIn("目覚ましより先に", edits[0]["ai"])
        self.assertIn("目覚ましより早く", edits[0]["human"])
        with self.assertRaises(ValueError):
            writing.save_human_edit(ART, edited)  # 変更なし

    def test_direct_file_edit_blocks_generation_until_saved(self):
        self.generate()
        p = writing.draft_path(ART)
        p.write_text(p.read_text(encoding="utf-8") + "\n手で足した段落。\n", encoding="utf-8")
        self.assertTrue(writing.file_changed(ART))
        with self.assertRaises(writing.Conflict):
            writing.prepare_generate(ART, "夜")
        writing.save_human_edit(ART, p.read_text(encoding="utf-8"))
        self.assertFalse(writing.file_changed(ART))
        writing.prepare_generate(ART, "夜")

    def test_restore_and_diff(self):
        self.generate()
        text = writing.draft_path(ART).read_text(encoding="utf-8")
        writing.save_human_edit(ART, text.replace("外はまだ暗い。", "外は明るかった。"))
        meta = writing.restore(ART, 1)
        self.assertEqual((meta["v"], meta["source"], meta["restored_from"]), (3, "restore", 1))
        self.assertEqual(writing.draft_path(ART).read_text(encoding="utf-8"), text)
        ops = writing.diff(ART, 1, 2)
        changed = [o for o in ops if o["op"] != "equal"]
        self.assertEqual(len(changed), 1)
        self.assertIn("外は明るかった。", changed[0]["new"][0])

    def test_generation_conflict_is_held_for_human(self):
        p = writing.prepare_generate(ART, "朝")
        self.generate()  # v1
        text = writing.draft_path(ART).read_text(encoding="utf-8")
        writing.save_human_edit(ART, text.replace("外はまだ暗い。", "人が直した。"))  # v2（朝の区間が変わった）
        self.set_result(paras(("別の結果。", [self.k1])))
        job = writing.run_job(dict(p, base_version=0), p["confirm_token"])  # v0 を元に作った結果
        self.assertEqual(job["status"], "conflict")
        self.assertEqual(writing.versions(ART)[-1]["v"], 2, "勝手に上書きしない")
        meta = writing.apply_conflicted(job["id"])
        self.assertEqual(meta["v"], 3)
        self.assertIn("別の結果。", writing.draft_path(ART).read_text(encoding="utf-8"))


class ReviseTest(WritingBase):
    def test_revise_is_proposal_until_accepted(self):
        self.generate()
        blocks = writing.current(ART)["blocks"]
        idx = next(i for i, b in enumerate(blocks) if b["text"].startswith("母が"))
        self.set_result(json.dumps({"text": "母が「もう起きたの」と言った。", "kakera": [self.k2]}, ensure_ascii=False))
        p = writing.prepare_revise(ART, idx, "かけらの言い回しに近づけて")
        job = writing.run_job(p, p["confirm_token"])
        self.assertEqual(job["status"], "done", job.get("error"))
        self.assertIn("かけらの言い回しに近づけて", self.last_call()["stdin"])
        self.assertEqual(writing.versions(ART)[-1]["v"], 1, "採用するまで本文は変わらない")
        meta = writing.accept_revision(job["id"])
        self.assertEqual((meta["v"], meta["source"], meta["block"]), (2, "ai_revise", idx))
        self.assertEqual(writing.current(ART)["blocks"][idx]["text"], "母が「もう起きたの」と言った。")
        with self.assertRaises(ValueError):
            writing.accept_revision(job["id"])

    def test_revise_conflict_when_text_changed(self):
        self.generate()
        blocks = writing.current(ART)["blocks"]
        idx = next(i for i, b in enumerate(blocks) if b["kind"] == "para")
        self.set_result(json.dumps({"text": "直した段落。", "kakera": [self.k1]}, ensure_ascii=False))
        p = writing.prepare_revise(ART, idx, "短く")
        job = writing.run_job(p, p["confirm_token"])
        text = writing.draft_path(ART).read_text(encoding="utf-8")
        writing.save_human_edit(ART, text.replace("外はまだ暗い。", "先に直した。"))
        with self.assertRaises(writing.Conflict):
            writing.accept_revision(job["id"])

    def test_heading_cannot_be_revised(self):
        self.generate()
        with self.assertRaises(ValueError):
            writing.prepare_revise(ART, 0, "直して")


class UnitTest(unittest.TestCase):
    def test_unsupported_terms(self):
        src = ["窓の外は3時ごろ暗かった。母は「早いね」と言った。スマートフォンを見た。"]
        self.assertEqual(writing.unsupported_terms("３時に「早いね」。スマートフォン。", src), [])
        got = writing.unsupported_terms("5年前、「帰ろう」と言った。タクシーに乗った。[要追加：ドライバーの名前]", src)
        self.assertEqual(got, ["「帰ろう」", "5年", "タクシー"])

    def test_split_and_render(self):
        blocks = writing.split_blocks("# 題\n## 区間\n一行目\n二行目\n\n\n次の段落\n")
        self.assertEqual([b["kind"] for b in blocks], ["heading", "heading", "para", "para"])
        self.assertEqual(blocks[2]["text"], "一行目\n二行目")
        self.assertEqual(writing.render(blocks), "# 題\n\n## 区間\n\n一行目\n二行目\n\n次の段落\n")

    def test_slug(self):
        self.assertEqual(writing.slug("前編 / 事件"), "前編_事件")
        self.assertEqual(writing.slug("../.."), "article")


if __name__ == "__main__":
    unittest.main()
