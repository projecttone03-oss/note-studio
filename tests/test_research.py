"""notewriter のリサーチ機能（research / perplexity / secrets）を確認する。

本物の Claude・Perplexity は呼ばない（Perplexity は偽の transport、Claude は偽のコマンド）。
実行: python3 -m unittest discover tests
"""
import json
import os
import shutil
import stat
import sys
import tempfile
import textwrap
import unittest
from datetime import date, timedelta
from pathlib import Path

from notewriter import perplexity, research, secrets, store

FAKE_KEY = "pplx-TESTkey1234567890abcdef"


def ok_response(cost=None, text="## 候補1\n本文です。", results=None):
    usage = {"prompt_tokens": 1000, "completion_tokens": 2000, "total_tokens": 3000}
    if cost is not None:
        usage["cost"] = {"input_tokens_cost": 0.003, "output_tokens_cost": 0.03, "request_cost": 0.01,
                         "total_cost": cost}
    data = {"model": "sonar-pro", "choices": [{"message": {"role": "assistant", "content": text}}],
            "usage": usage,
            "search_results": results if results is not None else [
                {"title": "厚生労働省, 統計", "url": "https://www.mhlw.go.jp/toukei/a,b.html", "date": "2025-04-01"},
                {"title": "重複", "url": "https://www.mhlw.go.jp/toukei/a,b.html"},
                {"title": "e-Gov", "url": "https://elaws.e-gov.go.jp/", "last_updated": "2025-01-02"}]}
    return json.dumps(data).encode("utf-8")


class FakeTransport:
    def __init__(self, status=200, body=None):
        self.status = status
        self.body = body if body is not None else ok_response()
        self.calls = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append({"url": url, "headers": dict(headers), "body": json.loads(body.decode("utf-8")),
                           "timeout": timeout})
        return self.status, self.body


class ResearchTestBase(unittest.TestCase):
    def setUp(self):
        self._old = {k: os.environ.get(k) for k in ("NW_DATA_DIR", "PERPLEXITY_API_KEY", "NW_CLAUDE_CMD")}
        os.environ.pop("PERPLEXITY_API_KEY", None)
        os.environ.pop("NW_CLAUDE_CMD", None)
        self.tmp = tempfile.mkdtemp(prefix="notewriter-research-")
        os.environ["NW_DATA_DIR"] = self.tmp
        store.init_vault()
        self._old_transport = research.TRANSPORT

    def tearDown(self):
        research.TRANSPORT = self._old_transport
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_local_config(self, data):
        p = Path(self.tmp) / "config" / "research.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data), encoding="utf-8")


class SecretsTest(ResearchTestBase):
    def test_set_permissions_and_redact(self):
        self.assertFalse(secrets.has_api_key())
        self.assertEqual(secrets.api_key_source(), "")
        warns = secrets.set_api_key("  " + FAKE_KEY + "\n")
        self.assertEqual(warns, [])
        self.assertTrue(secrets.has_api_key())
        self.assertEqual(secrets.api_key_source(), "file")
        self.assertEqual(secrets.get_api_key(), FAKE_KEY)
        path = Path(self.tmp) / "secrets" / "perplexity_api_key"
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        red = secrets.redact("key=" + FAKE_KEY + " other=pplx-abcDEF123")
        self.assertNotIn(FAKE_KEY, red)
        self.assertNotIn("abcDEF123", red)
        self.assertIn("pplx-****", red)
        self.assertTrue(secrets.delete_api_key())
        self.assertFalse(secrets.delete_api_key())
        self.assertFalse(secrets.has_api_key())

    def test_invalid_and_warnings(self):
        with self.assertRaises(ValueError):
            secrets.set_api_key("   ")
        with self.assertRaises(ValueError):
            secrets.set_api_key("pplx-a\npplx-b")
        warns = secrets.set_api_key("sk-something")
        self.assertEqual(len(warns), 1)
        self.assertIn("pplx-", warns[0])

    def test_env_has_priority(self):
        secrets.set_api_key(FAKE_KEY)
        os.environ["PERPLEXITY_API_KEY"] = "pplx-FROMENV999"
        self.assertEqual(secrets.api_key_source(), "env")
        self.assertEqual(secrets.get_api_key(), "pplx-FROMENV999")
        self.assertNotIn("FROMENV999", secrets.redact("x pplx-FROMENV999 y"))

    def test_gitignore_has_secrets(self):
        text = (store.ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("secrets/", text)
        self.assertIn("*.key", text)


class PerplexityTest(ResearchTestBase):
    def cfg(self):
        return research.load_config()["providers"]["pplx_standard"]

    def test_success_with_api_cost(self):
        t = FakeTransport(body=ok_response(cost=0.0431))
        res = perplexity.call("質問", self.cfg(), FAKE_KEY, system="sys", transport=t)
        self.assertEqual(res.cost_source, "api")
        self.assertAlmostEqual(res.cost_usd, 0.0431)
        self.assertEqual(res.text, "## 候補1\n本文です。")
        self.assertEqual([s["url"] for s in res.sources],
                         ["https://www.mhlw.go.jp/toukei/a,b.html", "https://elaws.e-gov.go.jp/"])
        self.assertEqual(res.sources[1]["date"], "2025-01-02")
        call = t.calls[0]
        self.assertEqual(call["headers"]["Authorization"], "Bearer " + FAKE_KEY)
        self.assertEqual(call["body"]["model"], "sonar-pro")
        self.assertEqual(call["body"]["messages"][-1], {"role": "user", "content": "質問"})
        self.assertEqual(call["body"]["web_search_options"], {"search_context_size": "medium"})

    def test_success_computed_cost(self):
        t = FakeTransport(body=ok_response(cost=None))
        res = perplexity.call("質問", self.cfg(), FAKE_KEY, transport=t)
        self.assertEqual(res.cost_source, "computed")
        # 1000*3/1M + 2000*15/1M + 10/1000
        self.assertAlmostEqual(res.cost_usd, 0.003 + 0.03 + 0.01, places=6)

    def test_think_block_removed(self):
        t = FakeTransport(body=ok_response(text="<think>内部の考え</think>\n# 結果"))
        res = perplexity.call("q", self.cfg(), FAKE_KEY, transport=t)
        self.assertEqual(res.text, "# 結果")

    def test_errors_do_not_leak_key(self):
        leak = json.dumps({"error": {"message": "invalid key " + FAKE_KEY}}).encode()
        for status, word, retry in ((401, "残高", False), (429, "課金されません", True), (503, "一時的", True),
                                    (400, "受け付けません", False)):
            with self.subTest(status=status):
                with self.assertRaises(perplexity.PplxError) as cm:
                    perplexity.call("q", self.cfg(), FAKE_KEY, transport=FakeTransport(status, leak))
                self.assertNotIn(FAKE_KEY, str(cm.exception))
                self.assertNotIn("TESTkey", str(cm.exception))
                self.assertIn(word, str(cm.exception))
                self.assertEqual(cm.exception.status, status)
                self.assertEqual(cm.exception.retryable, retry)
        with self.assertRaises(perplexity.PplxError):
            perplexity.call("q", self.cfg(), "", transport=FakeTransport())

    def test_estimate_cost_range(self):
        low, high = perplexity.estimate_cost(research.load_config()["providers"]["pplx_deep"], "deep")
        self.assertGreater(low, 0)
        self.assertGreater(high, low)


class PromptAndWarningsTest(ResearchTestBase):
    def test_build_prompt_contains_only_query(self):
        from notewriter import kakera
        kakera.create_kakera("秘密のかけら本文XYZ", article="記事A")
        kakera.create_neta("秘密のネタ帳ABC")
        p = research.build_prompt("deep", "  児童扶養手当の所得制限 {today}  ")
        self.assertIn("児童扶養手当の所得制限 {today}", p)
        self.assertNotIn("XYZ", p)
        self.assertNotIn("ABC", p)
        self.assertNotIn("{query}", p)
        self.assertIn("note.com の /search", p)
        tpl = (store.CONFIG_DIR / "research_prompts" / "deep.md").read_text(encoding="utf-8")
        self.assertEqual(len(p), len(tpl.replace("{today}", date.today().isoformat())
                                     .replace("{query}", "児童扶養手当の所得制限 {today}")))
        with self.assertRaises(ValueError):
            research.build_prompt("deep", "  ")
        with self.assertRaises(ValueError):
            research.build_prompt("other", "x")

    def test_outbound_warnings(self):
        w = research.outbound_warnings("大阪で暮らす40代のシングルマザーの体験")
        self.assertTrue(w)
        self.assertTrue(all(x["category"] in ("属性", "属性の集中", "伏せ字") for x in w))
        self.assertTrue(any("大阪" in x["match"] for x in w))
        self.assertEqual(research.outbound_warnings("児童扶養手当の制度"), [])


class BudgetAndJobTest(ResearchTestBase):
    def test_estimate_and_labels(self):
        e = research.estimate("claude", "trend")
        self.assertFalse(e["paid"])
        self.assertEqual(e["jpy_high"], 0)
        e = research.estimate("pplx_deep", "deep")
        self.assertTrue(e["paid"])
        self.assertGreater(e["jpy_high"], e["jpy_low"])
        self.assertIn("要確認", e["pricing_note"])
        self.assertEqual(research.provider_label("claude"), "Claude（追加料金なし）")
        self.assertFalse(research.is_paid("claude"))
        self.assertTrue(research.is_paid("pplx_standard"))

    def test_local_config_deep_merge(self):
        self.write_local_config({"monthly_budget_jpy": 500,
                                 "providers": {"pplx_standard": {"model": "sonar", "pricing": {"input_per_m": 1}}}})
        cfg = research.load_config()
        self.assertEqual(cfg["monthly_budget_jpy"], 500)
        p = cfg["providers"]["pplx_standard"]
        self.assertEqual(p["model"], "sonar")
        self.assertEqual(p["pricing"]["input_per_m"], 1)
        self.assertEqual(p["pricing"]["output_per_m"], 15)
        self.assertEqual(p["label"], "Perplexity ふつう")

    def test_pplx_run_job_records_usage_and_material(self):
        secrets.set_api_key(FAKE_KEY)
        t = FakeTransport(body=ok_response(cost=0.02))
        research.TRANSPORT = t
        prep = research.prepare("deep", "pplx_standard", "児童扶養手当", article="記事A")
        self.assertTrue(prep["budget_ok"])
        self.assertTrue(prep["estimate"]["paid"])
        job = research.run_job("deep", "pplx_standard", "児童扶養手当", "記事A", prep["confirm_token"])
        self.assertEqual(job["status"], "done", job["error"])
        self.assertEqual(t.calls[0]["body"]["messages"][-1]["content"], prep["prompt"])
        m = research.get_material(job["material_id"])
        self.assertEqual(m["article"], "記事A")
        self.assertEqual(m["provider"], "pplx_standard")
        self.assertEqual(m["model"], "sonar-pro")
        self.assertEqual(m["sources"][0]["url"], "https://www.mhlw.go.jp/toukei/a,b.html")
        self.assertEqual(m["sources"][0]["title"], "厚生労働省, 統計")
        self.assertAlmostEqual(m["cost_jpy"], 3.0)
        mu = research.month_usage()
        self.assertEqual(mu["count"], 1)
        self.assertAlmostEqual(mu["total_jpy"], 3.0)
        self.assertAlmostEqual(mu["remaining_jpy"], 997.0)
        self.assertEqual(research.get_job(job["id"])["status"], "done")
        self.assertEqual(research.list_jobs()[0]["id"], job["id"])
        text = Path(m["path"]).read_text(encoding="utf-8")
        self.assertNotIn(FAKE_KEY, text)
        usage_text = (Path(self.tmp) / "research" / "usage.json").read_text(encoding="utf-8")
        self.assertNotIn(FAKE_KEY, usage_text)

    def test_pplx_401_job_error_without_key(self):
        secrets.set_api_key(FAKE_KEY)
        research.TRANSPORT = FakeTransport(401, json.dumps({"error": {"message": FAKE_KEY}}).encode())
        prep = research.prepare("trend", "pplx_standard", "家計")
        job = research.run_job("trend", "pplx_standard", "家計", "", prep["confirm_token"])
        self.assertEqual(job["status"], "error")
        self.assertIn("残高", job["error"])
        self.assertNotIn(FAKE_KEY, json.dumps(job, ensure_ascii=False))
        self.assertEqual(research.month_usage()["total_jpy"], 0)

    def test_pplx_5xx_records_unknown_cost(self):
        secrets.set_api_key(FAKE_KEY)
        research.TRANSPORT = FakeTransport(502, b"bad gateway")
        prep = research.prepare("trend", "pplx_standard", "家計")
        job = research.run_job("trend", "pplx_standard", "家計", "", prep["confirm_token"])
        self.assertEqual(job["status"], "error")
        mu = research.month_usage()
        self.assertEqual(mu["unknown_count"], 1)
        self.assertGreater(mu["unknown_estimated_jpy"], 0)
        self.assertTrue(mu["runs"][0]["cost_unknown"])

    def test_confirm_mismatch(self):
        secrets.set_api_key(FAKE_KEY)
        research.TRANSPORT = FakeTransport()
        prep = research.prepare("deep", "pplx_standard", "児童扶養手当", article="記事A")
        with self.assertRaises(research.ConfirmMismatch):
            research.run_job("deep", "pplx_standard", "児童扶養手当（変更）", "記事A", prep["confirm_token"])
        with self.assertRaises(research.ConfirmMismatch):
            research.run_job("deep", "pplx_deep", "児童扶養手当", "記事A", prep["confirm_token"])
        with self.assertRaises(research.ConfirmMismatch):
            research.run_job("deep", "pplx_standard", "児童扶養手当", "記事B", prep["confirm_token"])
        self.assertEqual(research.TRANSPORT.calls, [])

    def test_budget_exceeded(self):
        secrets.set_api_key(FAKE_KEY)
        research.TRANSPORT = FakeTransport()
        self.write_local_config({"monthly_budget_jpy": 100})
        prep = research.prepare("deep", "pplx_deep", "制度")
        self.assertFalse(prep["budget_ok"])
        self.assertIn("上限", prep["budget_message"])
        with self.assertRaises(research.BudgetExceeded):
            research.run_job("deep", "pplx_deep", "制度", "", prep["confirm_token"])
        self.assertEqual(research.TRANSPORT.calls, [])
        # 既に使った分で上限に達している
        self.write_local_config({"monthly_budget_jpy": 1000})
        research._record_usage({"provider": "pplx_standard", "cost_usd": 7.0, "cost_jpy": 1000.0,
                                "cost_unknown": False})
        ok, msg = research.budget_check("pplx_standard", "trend")
        self.assertFalse(ok)
        self.assertIn("達しています", msg)
        self.assertEqual(research.budget_check("claude", "trend"), (True, ""))

    def test_claude_limit_does_not_switch(self):
        script = Path(self.tmp) / "fake_claude_limit.py"
        script.write_text(textwrap.dedent("""
            import json, sys
            sys.stdin.read()
            print(json.dumps({"type": "system", "subtype": "init", "tools": ["WebFetch", "WebSearch"],
                              "mcp_servers": [], "permissionMode": "dontAsk"}), flush=True)
            print(json.dumps({"type": "rate_limit_event",
                              "rate_limit_info": {"status": "rejected", "resetsAt": 1900000000}}), flush=True)
        """), encoding="utf-8")
        os.environ["NW_CLAUDE_CMD"] = sys.executable + " " + str(script)
        research.TRANSPORT = FakeTransport()
        prep = research.prepare("trend", "claude", "家計")
        job = research.run_job("trend", "claude", "家計", "", prep["confirm_token"])
        self.assertEqual(job["status"], "limit")
        self.assertTrue(job["limit_resets_at"])
        self.assertGreater(job["estimate_jpy_for_pplx"], 0)
        self.assertEqual(research.TRANSPORT.calls, [])

    def test_claude_success_via_start_job(self):
        script = Path(self.tmp) / "fake_claude_ok.py"
        script.write_text(textwrap.dedent("""
            import json, sys
            sys.stdin.read()
            print(json.dumps({"type": "system", "subtype": "init", "tools": ["WebSearch", "WebFetch"],
                              "mcp_servers": [], "permissionMode": "dontAsk", "model": "claude-test"}), flush=True)
            body = "# 候補\\n根拠: https://www.mhlw.go.jp/a.html と [内閣府](https://www.cao.go.jp/b)。"
            print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": body,
                              "usage": {"server_tool_use": {"web_search_requests": 3, "web_fetch_requests": 2}}}),
                  flush=True)
        """), encoding="utf-8")
        os.environ["NW_CLAUDE_CMD"] = sys.executable + " " + str(script)
        prep = research.prepare("trend", "claude", "家計")
        jid = research.start_job("trend", "claude", "家計", "", prep["confirm_token"])
        import time
        for _ in range(200):
            job = research.get_job(jid)
            if job["status"] != "running":
                break
            time.sleep(0.05)
        self.assertEqual(job["status"], "done", job["error"])
        m = research.get_material(job["material_id"])
        self.assertEqual(m["model"], "claude-test")
        self.assertEqual([s["url"] for s in m["sources"]], ["https://www.cao.go.jp/b", "https://www.mhlw.go.jp/a.html"])
        self.assertIn("/_trend/", m["path"])
        run = research.month_usage()["runs"][0]
        self.assertEqual(run["cost_jpy"], 0.0)
        self.assertEqual(run["web_search_requests"], 3)

    def test_one_job_at_a_time(self):
        prep = research.prepare("trend", "claude", "家計")
        fd = research._try_run_lock()
        try:
            with self.assertRaises(ValueError):
                research.run_job("trend", "claude", "家計", "", prep["confirm_token"])
        finally:
            research._release(fd)

    def test_orphan_running_job_becomes_error(self):
        store.write_json(Path(self.tmp) / "research" / "jobs" / "J0007.json",
                         {"id": "J0007", "status": "running", "kind": "trend", "provider": "claude"})
        self.assertEqual(research.get_job("J0007")["status"], "error")
        with self.assertRaises(KeyError):
            research.get_job("../x")

    def test_month_usage_by_month(self):
        path = Path(self.tmp) / "research" / "usage.json"
        store.write_json(path, [
            {"id": "U0001", "at": "2026-08-10T10:00:00", "month": "2026-08", "cost_usd": 1, "cost_jpy": 150},
            {"id": "U0002", "at": "2026-09-01T10:00:00", "month": "2026-09", "cost_usd": 0.1, "cost_jpy": 15},
            {"id": "U0003", "at": "2026-09-02T10:00:00", "month": "2026-09", "cost_usd": 0.2, "cost_jpy": 30},
        ])
        aug = research.month_usage("2026-08")
        self.assertEqual((aug["count"], aug["total_jpy"]), (1, 150))
        sep = research.month_usage("2026-09")
        self.assertEqual((sep["count"], sep["total_jpy"]), (2, 45))
        self.assertEqual(sep["runs"][0]["id"], "U0003")
        with self.assertRaises(ValueError):
            research.month_usage("2026/09")


class MaterialTest(ResearchTestBase):
    def test_save_and_read_back(self):
        srcs = [{"title": "表, カンマ入り", "url": "https://example.go.jp/a?x=1,2&y=3", "date": "2025-01-01"},
                "https://example.go.jp/b", {"url": "https://example.go.jp/b"}]
        m = research.save_material("../../記事/名", "deep", "pplx_deep", "sonar-deep-research",
                                   "## 統計\n本文", srcs, query="改行\n入り: の問い")
        self.assertEqual(m["id"], "M0001")
        self.assertEqual(len(m["sources"]), 2)
        self.assertEqual(m["sources"][0], srcs[0])
        self.assertEqual(m["article"], "../../記事/名")
        self.assertTrue(Path(m["path"]).resolve().is_relative_to(Path(self.tmp).resolve() / "research")
                        if hasattr(Path, "is_relative_to") else str(Path(m["path"]).resolve()).startswith(
                            str((Path(self.tmp) / "research").resolve())))
        self.assertTrue(m["body"].startswith(research.NOTICE))
        self.assertIn("担当: Perplexity 徹底調査", m["body"])
        self.assertIn("モデル: sonar-deep-research", m["body"])
        self.assertEqual(m["query"], "改行 入り: の問い")
        self.assertFalse(m["stale"])
        self.assertEqual(m["title"], "統計")
        self.assertEqual([x["id"] for x in research.list_materials("../../記事/名")], ["M0001"])
        research.delete_material("M0001")
        with self.assertRaises(KeyError):
            research.get_material("M0001")
        m2 = research.save_material("記事", "deep", "claude", "", "x", [])
        self.assertEqual(m2["id"], "M0002")  # 削除後も番号を再利用しない

    def test_stale(self):
        old = date.today() - timedelta(days=400)
        research.save_material("記事", "deep", "claude", "", "古い", [], researched_at=old.isoformat())
        research.save_material("記事", "deep", "claude", "", "新しい", [])
        st = research.stale_materials()
        self.assertEqual(len(st), 1)
        self.assertEqual(st[0]["researched_at"], old.isoformat())
        self.assertGreaterEqual(st[0]["age_days"], 365)
        self.assertEqual(research.list_materials()[0]["researched_at"], date.today().isoformat())
        with self.assertRaises(ValueError):
            research.save_material("記事", "deep", "claude", "", "x", [], researched_at="昨日")

    def test_import_material(self):
        text = "# 手で調べたメモ\n\n参考: https://www.mhlw.go.jp/x.html）と [e-Gov](https://elaws.e-gov.go.jp/doc)\n"
        m = research.import_material("pro_export.md", text, article="記事A", researched_at="2026-01-05")
        self.assertEqual(m["origin"], "import")
        self.assertEqual(m["provider"], "手動取り込み")
        self.assertEqual(m["researched_at"], "2026-01-05")
        self.assertEqual({s["url"] for s in m["sources"]},
                         {"https://www.mhlw.go.jp/x.html", "https://elaws.e-gov.go.jp/doc"})
        self.assertIn("手で調べたメモ", m["body"])
        self.assertTrue(m["body"].startswith(research.NOTICE))
        with self.assertRaises(ValueError):
            research.import_material("a.txt", "x")
        with self.assertRaises(ValueError):
            research.import_material("big.md", "あ" * (research.IMPORT_MAX_BYTES // 3 + 10))
        m2 = research.import_material("today.md", "本文だけ")
        self.assertEqual(m2["researched_at"], date.today().isoformat())


if __name__ == "__main__":
    unittest.main()
