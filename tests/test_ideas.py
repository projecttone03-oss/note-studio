"""notewriter のネタ出し機能（ideas / reactions と、その画面・CLI）を確認する。

本物の Claude は呼ばない。NW_CLAUDE_CMD にテスト内で作った偽の claude（stdin を保存し、stream-json を出す Python）を指定する。
実行: python3 -m unittest discover tests
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

from notewriter import claude_runner, ideas, kakera, reactions, store

ROOT = Path(__file__).resolve().parent.parent

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
    tools = ["WebSearch"] if mode == "websearch" else []
    emit({"type": "system", "subtype": "init", "tools": tools, "mcp_servers": [], "permissionMode": "dontAsk",
          "model": "claude-fake-ideas"})
    if mode == "limit":
        emit({"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "resetsAt": 1900000000}})
        sys.exit(0)
    with open(os.environ["FAKE_RESULT"], encoding="utf-8") as f:
        text = f.read()
    emit({"type": "result", "subtype": "success", "is_error": False, "result": text, "duration_ms": 10})
''')


def cand(idea, why="料理の経験と結びつく", skills=("料理",), reader="忙しい人", typ="体験談", points=("検索の多さ",)):
    return {"idea": idea, "why_me": why, "skills": list(skills), "reader": reader, "type": typ,
            "check_points": list(points)}


def fenced(cands, before="候補です。"):
    return before + "\n```json\n" + json.dumps({"candidates": cands}, ensure_ascii=False) + "\n```\n以上です。"


class IdeasBase(unittest.TestCase):
    ENV = ("NW_DATA_DIR", "NW_CLAUDE_CMD", "FAKE_MODE", "FAKE_OUT", "FAKE_RESULT")

    def setUp(self):
        self._old = {k: os.environ.get(k) for k in self.ENV}
        self.tmp = Path(tempfile.mkdtemp(prefix="nw-ideas-"))
        self.vault = self.tmp / "vault"
        os.environ["NW_DATA_DIR"] = str(self.vault)
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
        self.set_result(fenced([cand("時短の作り置き"), cand("引っ越しの段取り", typ="リサーチ型")]))

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
        files = sorted(self.calls.glob("call-*.json"))
        self.assertTrue(files, "偽の claude が呼ばれていません")
        return json.loads(files[-1].read_text(encoding="utf-8"))

    def run_ideas(self):
        return ideas.run_job(ideas.prepare()["confirm_token"])


class IdeasLogicTest(IdeasBase):
    def test_run_saves_candidates(self):
        job = self.run_ideas()
        self.assertEqual(job["status"], "done", job["error"])
        self.assertEqual(job["count"], 2)
        s = ideas.get_session(job["session_id"])
        self.assertEqual(s["id"], "I0001")
        self.assertEqual(s["model"], "claude-fake-ideas")
        self.assertEqual([c["cid"] for c in s["candidates"]], ["I0001-01", "I0001-02"])
        self.assertEqual({c["status"] for c in s["candidates"]}, {"未検討"})
        self.assertEqual(s["candidates"][1]["type"], "リサーチ型")
        self.assertIn("materials_summary", s)
        self.assertGreater(s["prompt_chars"], 100)
        self.assertNotIn("raw", s)
        self.assertEqual(Path(ideas._session_path("I0001")).parent, self.vault / "ideas")
        self.assertEqual(len(ideas.list_candidates("未検討")), 2)
        # Claude の作業ディレクトリはリサーチ用とは別
        self.assertTrue((self.vault / "ideas" / ".claude-cwd").is_dir())
        self.assertEqual(list((self.vault / "ideas" / ".claude-cwd").iterdir()), [])

    def test_materials_in_prompt_and_kakera_not(self):
        ideas.save_skills("・料理が得意SKILLMARK\n・引っ越しを5回した")
        kakera.create_neta("ネタ帳のメモNETAMARK")
        n2 = kakera.create_neta("かけらにしたネタPROMOTEDMARK")
        kakera.promote_neta(n2["id"], article="前編")
        kakera.create_kakera("かけらの本文KAKERAMARK 取調べの朝のこと", article="前編", section="朝")
        reactions.add_reaction("反応のよい記事REACTMARK", likes=50, comments=3, purchases=4, memo="メモMEMOMARK")
        reactions.add_reaction("いまいちの記事", likes=1)
        # 1回目: 候補を出して1つを却下する
        self.set_result(fenced([cand("過去の候補PASTMARK"), cand("却下する ネタ X")]))
        self.assertEqual(self.run_ideas()["status"], "done")
        ideas.set_status("I0001-02", "却下")
        # 2回目: 送った全文を確認
        prep = ideas.prepare()
        self.set_result(fenced([cand("却下するネタ　Ｘ"), cand("新しい候補")]))
        job = ideas.run_job(prep["confirm_token"])
        stdin = self.last_call()["stdin"]
        self.assertEqual(stdin, prep["prompt"])
        for s in ("SKILLMARK", "NETAMARK", "REACTMARK", "スキ 50", "購入 4", "PASTMARK", "却下する ネタ X",
                  date.today().isoformat()):
            self.assertIn(s, stdin)
        for s in ("KAKERAMARK", "取調べの朝", "PROMOTEDMARK", "MEMOMARK"):
            self.assertNotIn(s, stdin)
        # 反応がよかった順
        self.assertLess(stdin.index("REACTMARK"), stdin.index("いまいちの記事"))
        # 却下の節に入っている（過去候補の節には入れない）
        rej_part = stdin[stdin.index("## 却下したネタ"):]
        self.assertIn("却下する ネタ X", rej_part)
        past_part = stdin[stdin.index("## すでに出した候補"):stdin.index("## 却下したネタ")]
        self.assertNotIn("却下する ネタ X", past_part)
        # 却下済みと同じネタ（全角・空白違い）は保存しない
        self.assertEqual(job["status"], "done")
        self.assertEqual(job["excluded_rejected"], 1)
        s = ideas.get_session(job["session_id"])
        self.assertEqual([c["idea"] for c in s["candidates"]], ["新しい候補"])
        self.assertEqual(s["excluded_rejected"], 1)
        summ = s["materials_summary"]
        self.assertEqual((summ["neta"], summ["reactions"], summ["past"], summ["rejected"]), (1, 2, 1, 1))
        self.assertEqual(summ["skills_chars"], len(ideas.get_skills().strip()))
        # 材料の本文は保存しない
        text = ideas._session_path(job["session_id"]).read_text(encoding="utf-8")
        for s in ("SKILLMARK", "NETAMARK", "REACTMARK"):
            self.assertNotIn(s, text)
        self.assertEqual(ideas.rejected_ideas(), ["却下する ネタ X"])

    def test_command_line_has_no_tools(self):
        self.run_ideas()
        rec = self.last_call()
        argv = rec["argv"]
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertNotIn("--allowedTools", argv)
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "dontAsk")
        for flag in ("--strict-mcp-config", "--settings", "--no-session-persistence", "-p"):
            self.assertIn(flag, argv)
        self.assertNotIn("--bare", argv)
        deny = argv[argv.index("--disallowedTools") + 1].split(",")
        for t in ("WebSearch", "WebFetch", "Bash", "Read"):
            self.assertIn(t, deny)
        self.assertEqual(argv[argv.index("--max-turns") + 1], "2")
        self.assertEqual(rec["allowed"], "")
        settings = json.loads(argv[argv.index("--settings") + 1])
        self.assertIn("tool_guard.py", settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"])
        self.assertFalse(any("料理" in a or "ネタ候補" in a for a in argv))  # 材料は引数に出さない

    def test_websearch_in_init_is_rejected(self):
        os.environ["FAKE_MODE"] = "websearch"
        job = self.run_ideas()
        self.assertEqual(job["status"], "error")
        self.assertIn("安全のため", job["error"])
        self.assertEqual(ideas.list_sessions(), [])
        with self.assertRaises(claude_runner.ToolVerificationError):
            claude_runner.run_claude("x", (), max_turns=2, timeout_sec=30, cwd=self.tmp / "cwd")

    def test_tool_guard_denies_all_when_empty(self):
        env = dict(os.environ, NW_ALLOWED_TOOLS="")
        for name in ("WebSearch", "WebFetch", "Bash", "Read"):
            r = subprocess.run([sys.executable, str(claude_runner.HOOK_SCRIPT)], input=json.dumps({"tool_name": name}),
                               env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 2, name)
            self.assertIn("ツールを使えません", r.stderr)

    def test_parse_failure_keeps_raw(self):
        self.set_result("ごめんなさい、JSON にできませんでした。候補A、候補B")
        job = self.run_ideas()
        self.assertEqual(job["status"], "error")
        self.assertIn("読み取れません", job["error"])
        s = ideas.get_session(job["session_id"])
        self.assertEqual(s["candidates"], [])
        self.assertIn("候補A", s["raw"])
        self.assertEqual(ideas.list_candidates(), [])

    def test_parse_candidates(self):
        got = ideas.parse_candidates(fenced([cand("a", typ="エッセイ"), {"idea": ""}, "x",
                                             {"idea": "b", "skills": "料理", "check_points": "需要"}]))
        self.assertEqual([c["idea"] for c in got], ["a", "b"])
        self.assertEqual(got[0]["type"], "不明")
        self.assertEqual(got[1]["skills"], ["料理"])
        self.assertEqual(got[1]["check_points"], ["需要"])
        self.assertEqual(got[1]["type"], "不明")
        bare = ideas.parse_candidates('前置き {"candidates": [{"idea": "c", "type": "リサーチ型"}]} 後書き')
        self.assertEqual((bare[0]["idea"], bare[0]["type"]), ("c", "リサーチ型"))
        for bad in ("", "no json", '{"candidates": []}', '{"x": 1}'):
            with self.assertRaises(ValueError):
                ideas.parse_candidates(bad)

    def test_status_changes(self):
        self.run_ideas()
        c = ideas.set_status("I0001-01", "保留")
        self.assertEqual(c["status"], "保留")
        self.assertEqual(ideas.get_candidate("I0001-01")["status"], "保留")
        self.assertEqual(ideas.counts_by_status()["保留"], 1)
        with self.assertRaises(ValueError):
            ideas.set_status("I0001-01", "採用")
        for bad in ("I0001-99", "I0009-01", "../x", ""):
            with self.assertRaises(KeyError):
                ideas.set_status(bad, "保留")
        with self.assertRaises(KeyError):
            ideas.set_statuses(["I0001-02", "I0001-99"], "保留")
        self.assertEqual(ideas.get_candidate("I0001-02")["status"], "未検討")  # 途中まで変えない
        ideas.set_statuses(["I0001-01", "I0001-02"], "調査に回した")
        self.assertEqual(len(ideas.list_candidates("調査に回した")), 2)

    def test_research_query_uses_only_idea_and_points(self):
        ideas.save_skills("・秘密の経験SKILLSECRET")
        self.set_result(fenced([cand("作り置きのコツ", why="WHYSECRET の経験がある", skills=("SKILLSECRET",),
                                     reader="READERX", points=("季節で検索が増えるか", "競合の数")),
                                cand("引っ越しの手順", why="WHYSECRET2", points=())]))
        self.run_ideas()
        q = ideas.research_query(["I0001-01", "I0001-02", "I0001-01"])
        self.assertTrue(q.startswith("次のネタ候補について、今の需要を確かめたい。\n1. 作り置きのコツ"))
        self.assertIn("確かめたい点: 季節で検索が増えるか／競合の数", q)
        self.assertIn("\n2. 引っ越しの手順", q)
        self.assertNotIn("3.", q)
        for s in ("WHYSECRET", "SKILLSECRET", "READERX"):
            self.assertNotIn(s, q)
        with self.assertRaises(ValueError):
            ideas.research_query([])

    def test_limit_stops_without_session(self):
        os.environ["FAKE_MODE"] = "limit"
        job = self.run_ideas()
        self.assertEqual(job["status"], "limit")
        self.assertIn("Claude の利用上限に達しました", job["error"])
        self.assertIn("ごろ解除", job["error"])
        self.assertIn("時間をおいてもう一度", job["error"])
        self.assertNotIn("Perplexity", job["error"])
        self.assertEqual(ideas.list_sessions(), [])

    def test_confirm_token(self):
        with self.assertRaises(ideas.ConfirmMismatch):
            ideas.run_job("bad")
        prep = ideas.prepare()
        kakera.create_neta("確認のあとに足したネタ")
        with self.assertRaises(ideas.ConfirmMismatch):
            ideas.run_job(prep["confirm_token"])
        self.assertEqual(list(self.calls.glob("call-*.json")), [])

    def test_one_job_at_a_time_and_start_job(self):
        fd = ideas._try_run_lock()
        try:
            with self.assertRaises(ValueError):
                self.run_ideas()
        finally:
            ideas._release(fd)
        jid = ideas.start_job(ideas.prepare()["confirm_token"])
        for _ in range(400):
            job = ideas.get_job(jid)
            if job["status"] != "running":
                break
            time.sleep(0.05)
        self.assertEqual(job["status"], "done", job["error"])
        with self.assertRaises(KeyError):
            ideas.get_job("../x")

    def test_skills_saved_in_vault_only(self):
        ideas.save_skills("・料理が得意\r\n・本が好き\n\n")
        self.assertEqual(ideas.get_skills(), "・料理が得意\n・本が好き")
        path = self.vault / "profile" / "skills.md"
        self.assertTrue(path.is_file())
        self.assertFalse(str(path.resolve()).startswith(str(ROOT.resolve()) + os.sep))
        self.assertFalse((ROOT / "profile").exists())
        self.assertFalse((ROOT / "skills.md").exists())
        ideas.save_skills("")
        self.assertEqual(ideas.get_skills(), "")

    def test_local_config_override(self):
        store.write_json(self.vault / "config" / "ideas.json", {"candidate_count": 7, "neta_max_items": 1,
                                                                "neta_max_chars": 5})
        kakera.create_neta("古いネタOLD")
        kakera.create_neta("新しいネタ0123456789")
        prompt = ideas.build_prompt()
        self.assertIn("7 個前後", prompt)
        self.assertIn("- 新しいネタ…", prompt)
        self.assertNotIn("古いネタOLD", prompt)
        self.assertEqual(ideas.load_config()["max_turns"], 2)


class ReactionsTest(IdeasBase):
    def test_add_update_delete_and_order(self):
        a = reactions.add_reaction("記事A", published="2026-09-01", recorded="2026-09-02", likes="10", comments=1,
                                   purchases=0)
        b = reactions.add_reaction("記事B", likes=3, purchases=2, memo="メモ")  # 記録日は今日
        reactions.add_reaction("記事A", recorded="2026-09-20", likes=30, purchases=3)
        self.assertEqual(a["id"], "R0001")
        self.assertEqual(b["recorded"], date.today().isoformat())
        top = reactions.top_reactions(5)
        self.assertEqual([r["title"] for r in top], ["記事A", "記事B"])  # 最新の記録（購入3）で比べる
        self.assertEqual(top[0]["likes"], 30)
        self.assertEqual(top[0]["published"], "2026-09-01")
        self.assertNotIn("memo", top[1])
        groups = reactions.by_article()
        self.assertEqual(len(groups[0]["records"]), 2)
        u = reactions.update_reaction("R0002", purchases=10)
        self.assertEqual((u["purchases"], u["title"], u["memo"]), (10, "記事B", "メモ"))
        self.assertEqual(reactions.top_reactions(1)[0]["title"], "記事B")
        reactions.delete_reaction("R0002")
        with self.assertRaises(KeyError):
            reactions.delete_reaction("R0002")
        with self.assertRaises(KeyError):
            reactions.get_reaction("bad")
        for bad in ({"title": ""}, {"title": "x", "likes": -1}, {"title": "x", "likes": "たくさん"},
                    {"title": "x", "published": "9/1"}):
            with self.assertRaises(ValueError):
                reactions.add_reaction(**bad)
        with self.assertRaises(ValueError):
            reactions.update_reaction("R0001", unknown=1)
        self.assertTrue((self.vault / "reactions.json").is_file())


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(_NoRedirect)


class IdeasWebTest(IdeasBase):
    @classmethod
    def setUpClass(cls):
        from notewriter import web
        cls.web = web
        cls.httpd = web.make_server("127.0.0.1", 0)
        cls.httpd.quiet = True
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        super().setUp()
        store.write_json(self.vault / self.web.NOTICE_FILE, {"notice_version": self.web.NOTICE_VERSION})

    def get(self, path):
        try:
            with OPENER.open(self.base + path) as r:
                return r.status, r.read().decode("utf-8"), r.headers
        except urllib.error.HTTPError as ex:
            return ex.code, ex.read().decode("utf-8"), ex.headers

    def post(self, path, fields=None):
        data = urllib.parse.urlencode(fields or {}, doseq=True).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data, method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with OPENER.open(req) as r:
                return r.status, r.read().decode("utf-8"), r.headers
        except urllib.error.HTTPError as ex:
            return ex.code, ex.read().decode("utf-8"), ex.headers

    def follow(self, headers):
        loc = headers.get("Location")
        self.assertTrue(loc and loc.startswith("/"), loc)
        return self.get(loc)

    def test_ideas_page_and_cards(self):
        status, body, _ = self.get("/ideas")
        self.assertEqual(status, 200)
        self.assertIn('href="/ideas"', body)
        self.assertIn("まだ書いていません", body)
        self.assertIn("かけら（体験談の素材）は使いません", body)
        self.set_result(fenced([cand("<b>作り置き</b>", why="WHY理由"), cand("引っ越し", typ="リサーチ型")]))
        self.run_ideas()
        _, body, _ = self.get("/ideas")
        self.assertIn("仮説（まだ需要を確かめていない）", body)
        self.assertIn("&lt;b&gt;作り置き&lt;/b&gt;", body)
        self.assertNotIn("<b>作り置き</b>", body)
        self.assertIn("WHY理由", body)
        self.assertIn("リサーチ型", body)
        self.assertIn('name="cids" value="I0001-01"', body)
        self.assertIn("選んだ候補をトレンド調査に回す", body)
        self.assertIn("未検討 2", body)
        _, home, _ = self.get("/")
        self.assertIn("未検討のネタ候補 <b>2</b>", home)
        _, guide, _ = self.get("/guide")
        self.assertIn("ネタ出しの使い方", guide)
        self.assertIn("本名・会社名・住所など", guide)

    def test_confirm_shows_full_prompt_and_run(self):
        ideas.save_skills("・料理が得意<SKILL>")
        status, body, _ = self.post("/ideas/confirm")
        self.assertEqual(status, 200)
        self.assertIn("Claude に渡す文章（これが全文です）", body)
        self.assertIn("料理が得意&lt;SKILL&gt;", body)
        self.assertIn("もう一度確認画面", body)
        self.assertIn("OK。ネタを出す（追加料金なし）", body)
        token = re.search(r'name="confirm_token" value="([0-9a-f]+)"', body).group(1)
        status, _, headers = self.post("/ideas/run", {"confirm_token": token})
        self.assertEqual(status, 303)
        self.assertRegex(headers["Location"], r"^/ideas/jobs/IJ\d+")
        jid = re.search(r"IJ\d+", headers["Location"]).group(0)
        for _ in range(400):
            if ideas.get_job(jid)["status"] != "running":
                break
            time.sleep(0.05)
        _, body, _ = self.get("/ideas/jobs/" + jid)
        self.assertIn("ネタの候補を <b>2</b> 件出しました", body)
        self.assertEqual(self.last_call()["stdin"].count("料理が得意<SKILL>"), 1)
        # 改ざんされたトークンでは実行しない
        status, _, headers = self.post("/ideas/run", {"confirm_token": "x"})
        self.assertEqual(status, 303)
        self.assertIn("err=", headers["Location"])
        _, hist, _ = self.get("/ideas/history")
        self.assertIn("I0001", hist)
        status, one, _ = self.get("/ideas/history/I0001")
        self.assertEqual(status, 200)
        self.assertIn("時短の作り置き", one)

    def test_to_research_prefills_and_marks(self):
        self.set_result(fenced([cand("作り置き", why="WHYSECRET", points=("検索が増える時期",)),
                                cand("引っ越し", why="WHYSECRET2")]))
        self.run_ideas()
        status, body, _ = self.post("/ideas/to-research", {"cids": ["I0001-01", "I0001-02"], "back": "/ideas"})
        self.assertEqual(status, 200)
        self.assertIn('action="/research/confirm"', body)
        m = re.search(r'<textarea id="r-query" name="query"[^>]*>(.*?)</textarea>', body, re.S)
        self.assertTrue(m)
        self.assertIn("次のネタ候補について、今の需要を確かめたい。", m.group(1))
        self.assertIn("1. 作り置き（確かめたい点: 検索が増える時期）", m.group(1))
        self.assertIn("2. 引っ越し", m.group(1))
        self.assertNotIn("WHYSECRET", body)
        self.assertRegex(body, r'value="trend" checked')
        self.assertEqual({c["status"] for c in ideas.list_candidates()}, {"調査に回した"})
        status, _, headers = self.post("/ideas/to-research", {"back": "/ideas"})
        self.assertEqual(status, 303)
        self.assertIn("err=", headers["Location"])

    def test_status_buttons(self):
        self.run_ideas()
        status, _, headers = self.post("/ideas/status", {"set": "I0001-01|却下", "cids": ["I0001-02"],
                                                         "back": "/ideas?status=未検討"})
        self.assertEqual(status, 303)
        self.assertTrue(headers["Location"].startswith("/ideas?status="))
        self.assertEqual(ideas.get_candidate("I0001-01")["status"], "却下")
        self.assertEqual(ideas.get_candidate("I0001-02")["status"], "未検討")
        _, body, _ = self.get("/ideas?status=" + urllib.parse.quote("却下"))
        self.assertIn("I0001-01", body)
        self.assertNotIn('name="cids" value="I0001-01"', body)  # 却下は選べない
        self.post("/ideas/status", {"set": "I0001-01|未検討", "back": "//evil.example/"})
        self.assertEqual(ideas.get_candidate("I0001-01")["status"], "未検討")
        status, _, headers = self.post("/ideas/status", {"set": "I0001-01|採用"})
        self.assertIn("err=", headers["Location"])

    def test_parse_error_job_page(self):
        self.set_result("読めない応答RAWTEXT")
        job = self.run_ideas()
        _, body, _ = self.get("/ideas/jobs/" + job["id"])
        self.assertIn("うまくいきませんでした", body)
        self.assertIn("もう一度ネタを出す", body)
        _, body, _ = self.get("/ideas/history/" + job["session_id"])
        self.assertIn("RAWTEXT", body)

    def test_limit_job_page(self):
        os.environ["FAKE_MODE"] = "limit"
        job = self.run_ideas()
        _, body, _ = self.get("/ideas/jobs/" + job["id"])
        self.assertIn("Claude の利用上限に達しました", body)
        self.assertNotIn("Perplexity で続ける", body)

    def test_skills_page(self):
        _, body, _ = self.get("/settings/skills")
        self.assertIn("お店で働いていた", body)
        self.assertIn("人に知られたくないことは書かなくて大丈夫", body)
        self.assertNotIn("placeholder=\"・10年", body)
        status, _, headers = self.post("/settings/skills", {"skills": "・料理が得意\r\n・本が好き"})
        self.assertEqual(status, 303)
        self.assertEqual(ideas.get_skills(), "・料理が得意\n・本が好き")
        self.assertTrue((self.vault / "profile" / "skills.md").is_file())
        _, body, _ = self.follow(headers)
        self.assertIn("・料理が得意\n・本が好き</textarea>", body)

    def test_reactions_page(self):
        status, _, headers = self.post("/reactions/new", {"title": "公開した記事<1>", "published": "2026-09-01",
                                                          "recorded": "2026-09-10", "likes": "12", "comments": "2",
                                                          "purchases": "1", "memo": "よかった"})
        self.assertEqual(status, 303)
        _, body, _ = self.follow(headers)
        self.assertIn("公開した記事&lt;1&gt;", body)
        self.assertIn("よかった", body)
        rid = reactions.list_reactions()[0]["id"]
        self.post(f"/reactions/{rid}", {"title": "公開した記事<1>", "likes": "20", "recorded": "2026-09-10"})
        self.assertEqual(reactions.get_reaction(rid)["likes"], 20)
        status, _, headers = self.post("/reactions/new", {"title": "x", "likes": "-3"})
        self.assertIn("err=", headers["Location"])
        self.post(f"/reactions/{rid}/delete")
        self.assertEqual(reactions.list_reactions(), [])


class IdeasCliTest(IdeasBase):
    def run_nw(self, args, stdin=""):
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        env.pop("EDITOR", None)
        env.pop("VISUAL", None)
        return subprocess.run([sys.executable, "-m", "notewriter"] + args, input=stdin, capture_output=True,
                              text=True, env=env, cwd=str(ROOT), start_new_session=True, timeout=60)

    def test_cli(self):
        r = self.run_nw(["skills", "edit"], stdin="・料理が得意CLI\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(ideas.get_skills(), "・料理が得意CLI")
        self.assertIn("料理が得意CLI", self.run_nw(["skills", "show"]).stdout)
        r = self.run_nw(["reaction", "add", "記事A", "--likes", "5", "--purchases", "1"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("記事A", self.run_nw(["reaction", "list"]).stdout)
        # 端末がないので確認できず、渡さない
        r = self.run_nw(["ideas", "run", "--show-prompt"], stdin="yes\n")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Claude に渡す文章", r.stdout)
        self.assertIn("料理が得意CLI", r.stdout)
        self.assertIn("渡しませんでした", r.stdout)
        self.assertEqual(list(self.calls.glob("call-*.json")), [])
        # 候補を作って一覧・状態変更・質問文
        self.run_ideas()
        r = self.run_nw(["ideas", "list", "--status", "未検討"])
        self.assertIn("I0001-01", r.stdout)
        self.assertIn("仮説", r.stdout)
        r = self.run_nw(["ideas", "set", "I0001-01", "却下"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(ideas.get_candidate("I0001-01")["status"], "却下")
        r = self.run_nw(["ideas", "to-research", "I0001-02"])
        self.assertIn("1. 引っ越しの段取り", r.stdout)
        self.assertNotIn("料理の経験", r.stdout)
        self.assertEqual(ideas.get_candidate("I0001-02")["status"], "未検討")  # 表示するだけ
        r = self.run_nw(["ideas", "set", "I0001-99", "保留"])
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
