"""コンプライアンスチェッカー（notewriter.compliance）のテスト。

警告を出すだけで本文を変えないこと、各カテゴリの検出/非検出、ルールの上書き結合を確かめる。
作業フォルダ（NW_DATA_DIR）は一時フォルダにして、他のテストや実データから独立させる。
実行: python3 -m unittest discover tests
"""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from notewriter import compliance as C


def cats(findings, category=None):
    return [f for f in findings if category is None or f.category == category]


def matches(findings, category):
    return [f.match for f in findings if f.category == category]


class ComplianceTestBase(unittest.TestCase):
    def setUp(self):
        self._old_env = os.environ.get("NW_DATA_DIR")
        self.tmp = tempfile.mkdtemp(prefix="nw-compliance-test-")
        os.environ["NW_DATA_DIR"] = self.tmp  # 作業フォルダは空（上書きルールなし・未初期化）
        self.rules = C.load_rules()

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("NW_DATA_DIR", None)
        else:
            os.environ["NW_DATA_DIR"] = self._old_env
        shutil.rmtree(self.tmp, ignore_errors=True)

    def check(self, text, name="テスト"):
        return C.check_text(text, self.rules, name=name)


class RulesTest(ComplianceTestBase):
    def test_default_rules_load_without_vault(self):
        self.assertEqual(self.rules["_sources"], [str(C.default_rules_path())])
        self.assertEqual(C.validate_rules(self.rules), [])
        self.assertEqual(self.rules["private_terms"], [])
        self.assertEqual(self.rules["attribute_cluster_threshold"], 3)
        for cat in ("時期", "地域", "勤務先", "家族構成", "学歴", "年齢"):
            self.assertIn(cat, self.rules["attributes"])

    def test_override_merge(self):
        cfg = Path(self.tmp) / "config"
        cfg.mkdir()
        (cfg / C.RULES_FILE).write_text(json.dumps({
            "_説明": "個人用",
            "forbidden_words": [{"word": "ダミー禁句", "note": "テスト用"}],
            "private_terms": ["山田太郎", {"word": "架空町", "note": "地元"}],
            "attribute_cluster_threshold": 2,
        }, ensure_ascii=False), encoding="utf-8")
        rules = C.load_rules()
        self.assertEqual(rules["_sources"], [str(C.default_rules_path()), str(cfg / C.RULES_FILE)])
        # キー単位で置き換え（既定の禁句は消える）
        self.assertEqual([w["word"] for w in rules["forbidden_words"]], ["ダミー禁句"])
        self.assertEqual(rules["attribute_cluster_threshold"], 2)
        # 上書きしていないキーは既定のまま
        self.assertEqual(rules["advice_patterns"], self.rules["advice_patterns"])
        # private_terms だけは既定と結合（既定 [] + 上書き）
        self.assertEqual(rules["private_terms"], ["山田太郎", {"word": "架空町", "note": "地元"}])
        self.assertEqual(C.validate_rules(rules), [])

        fs = C.check_text("相談した。ダミー禁句がある。山田太郎と架空町で会った。", rules)
        self.assertEqual(matches(fs, "禁句"), ["ダミー禁句"])
        strong = cats(fs, "伏せ字")
        self.assertEqual([f.match for f in strong], ["山田太郎", "架空町"])
        self.assertTrue(all(f.severity == "strong" for f in strong))
        self.assertIn("地元", strong[1].message)

    def test_private_terms_merge_with_default(self):
        # 既定側に private_terms があっても上書き側と結合される（重複は1つに）
        base = dict(self.rules)
        base["private_terms"] = ["既定の語"]
        cfg = Path(self.tmp) / "config"
        cfg.mkdir()
        (cfg / C.RULES_FILE).write_text(json.dumps({"private_terms": ["既定の語", "追加の語"]},
                                                   ensure_ascii=False), encoding="utf-8")
        orig = C.default_rules_path
        dflt = Path(self.tmp) / "default_rules.json"
        dflt.write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
        C.default_rules_path = lambda: dflt
        try:
            rules = C.load_rules()
        finally:
            C.default_rules_path = orig
        self.assertEqual(rules["private_terms"], ["既定の語", "追加の語"])

    def test_broken_override_file_is_reported(self):
        cfg = Path(self.tmp) / "config"
        cfg.mkdir()
        (cfg / C.RULES_FILE).write_text("{ 壊れた JSON", encoding="utf-8")
        rules = C.load_rules()
        problems = C.validate_rules(rules)
        self.assertTrue(any("読み込めません" in p for p in problems), problems)
        # 既定ルールだけで動き続ける
        self.assertTrue(matches(C.check_text("相談した。", rules), "禁句"))

    def test_validate_reports_broken_regex(self):
        rules = dict(self.rules)
        rules["advice_patterns"] = [{"pattern": "(しましょう", "note": "括弧が閉じていない"}]
        rules["attributes"] = {"時期": {"patterns": ["[0-9"]}, "年齢": {"patterns": ["\\d*"]}}
        rules["statistics"] = {"number_patterns": ["%"], "source_patterns": ["出典"], "year_patterns": ["年("]}
        rules["attribute_cluster_threshold"] = "3"
        rules["forbiden_words"] = []
        problems = C.validate_rules(rules)
        joined = "\n".join(problems)
        self.assertIn("advice_patterns[0]", joined)
        self.assertIn("attributes.時期.patterns[0]", joined)
        self.assertIn("attributes.年齢.patterns[0]", joined)  # 空文字列に一致する
        self.assertIn("statistics.year_patterns[0]", joined)
        self.assertIn("attribute_cluster_threshold", joined)
        self.assertIn("forbiden_words", joined)  # 綴り間違いのキー
        # 壊れたルールがあっても check_text は落ちない（その項目は飛ばす）
        C.check_text("しましょう。2024年1月1日。", rules)


class CheckTextTest(ComplianceTestBase):
    def test_text_is_never_modified(self):
        text = ("# 見出し\n\n私は相談を受けたほうがいいと思った。2019年3月、大阪で。\r\n"
                "[要追加：弁護士に相談した日]\n```\n相談\n```\n")
        before = str(text)
        fs = self.check(text)
        self.assertEqual(text, before)
        self.assertTrue(fs)
        self.assertTrue(all(isinstance(f, C.Finding) for f in fs))

    def test_forbidden_words(self):
        fs = self.check("弁護士に相談した。アドバイスをもらった。これで必ず勝てる。")
        self.assertEqual(matches(fs, "禁句"), ["相談", "アドバイス", "必ず"])
        f = cats(fs, "禁句")[0]
        self.assertEqual((f.line, f.start, f.end), (1, 4, 6))
        self.assertEqual(f.severity, "warn")
        self.assertIn("弁護士法", f.message)  # ルールの note を含む
        self.assertEqual(f.rule, "forbidden_words[0]:相談")
        self.assertEqual(f.to_dict()["category"], "禁句")

    def test_forbidden_word_exception(self):
        fs = self.check("必ずしも悪いことばかりではなかった。")
        self.assertEqual(matches(fs, "禁句"), [])

    def test_advice_patterns(self):
        fs = self.check("早めに動いたほうがいい。あなたも気をつけてください。記録を残しましょう。")
        self.assertEqual(matches(fs, "助言表現"), ["ほうがいい", "あなたも", "てください", "ましょう"])
        fs = self.check("私はもっと早く動くべきだった。どうすべきか迷った。")
        self.assertEqual(matches(fs, "助言表現"), [])

    def test_attributes_detected(self):
        fs = self.check("2019年3月、大阪で逮捕された。")
        self.assertEqual(matches(fs, "属性"), ["2019年3月", "大阪"])
        fs = self.check("株式会社サンプル商事で働いていた。")
        self.assertEqual(matches(fs, "属性"), ["株式会社サンプル商事で働"])
        fs = self.check("妻と子ども2人がいた。")
        self.assertEqual(matches(fs, "属性"), ["妻", "子ども2人"])
        fs = self.check("早稲田大学を中退した。")
        self.assertEqual(matches(fs, "属性"), ["早稲田大学", "中退"])
        fs = self.check("当時38歳、40代の上司がいた。")
        self.assertEqual(matches(fs, "属性"), ["38歳", "40代"])
        fs = self.check("横浜市中区の新宿駅ではなく、令和2年のこと。")
        self.assertEqual(matches(fs, "属性"), ["横浜市中区", "新宿駅", "令和2年"])

    def test_attributes_common_false_positives_avoided(self):
        text = ("株式市場は好調だった。区別がつかない。町内会の集まり。都市部に住む。大丈夫、工夫した。"
                "弟子入りした。姉妹都市。母国語。田村さんと中村さん。最寄り駅。各駅停車。会社で働いていた。"
                "国立大学。3割引で買った。")
        fs = self.check(text)
        self.assertEqual(matches(fs, "属性"), [])
        self.assertEqual(cats(fs, "出典"), [])

    def test_ignore_patterns(self):
        text = "今日は[要追加：相談した弁護士の名前と2019年3月の出来事]があった。"
        fs = self.check(text)
        self.assertEqual(fs, [])
        # 除外した後も行・位置はずれない
        text = "[要追加：相談]のあと、相談した。"
        f = cats(self.check(text), "禁句")[0]
        self.assertEqual(text[f.start:f.end], "相談")
        self.assertEqual(f.start, text.index("のあと") + 4)

    def test_code_block_excluded_and_headings_included(self):
        text = "# 相談の記録\n\n```\n相談 必ず 2019年3月 大阪で\n```\n本文。\n"
        fs = self.check(text)
        self.assertEqual([(f.line, f.match) for f in fs], [(1, "相談")])

    def test_attribute_cluster(self):
        text = "前置き。\n\n2019年3月、大阪で、\n株式会社サンプルで働いていた頃の話。\n\n別の段落。妻がいた。"
        fs = self.check(text)
        cl = cats(fs, "属性の集中")
        self.assertEqual(len(cl), 1)
        self.assertEqual(cl[0].line, 3)  # 段落の開始行
        self.assertEqual((cl[0].start, cl[0].end), (-1, -1))
        self.assertEqual(cl[0].match, "時期・地域・勤務先")
        self.assertEqual(cl[0].severity, "warn")
        # 2種類しかない段落では出ない
        self.assertEqual(cats(self.check("2019年3月、大阪で逮捕された。"), "属性の集中"), [])
        # しきい値はルールで変えられる
        rules = dict(self.rules, attribute_cluster_threshold=2)
        self.assertEqual(len(cats(C.check_text("2019年3月、大阪で逮捕された。", rules), "属性の集中")), 1)

    def test_statistics_source_and_year(self):
        fs = self.check("日本人の3人に1人が借金を抱えているという。")
        self.assertEqual(matches(fs, "出典"), ["3人に1人"])
        self.assertEqual(matches(fs, "年度"), ["3人に1人"])
        fs = self.check("失業率は2.6%だった（総務省「労働力調査」）。")
        self.assertEqual(matches(fs, "出典"), [])
        self.assertEqual(matches(fs, "年度"), ["2.6%"])
        fs = self.check("2023年には約3割の人が経験したという。")
        self.assertEqual(matches(fs, "出典"), ["3割"])
        self.assertEqual(matches(fs, "年度"), [])
        fs = self.check("自己破産は年間7万件ある。\n\n出典：最高裁判所「司法統計」令和5年")
        self.assertEqual(cats(fs, "出典") + cats(fs, "年度"), [])

    def test_personal_amount_is_not_statistics(self):
        fs = self.check("借金300万円があった。")
        self.assertEqual(cats(fs, "出典") + cats(fs, "年度"), [])

    def test_private_terms(self):
        rules = dict(self.rules, private_terms=["山田太郎", {"pattern": "架空[町村]"}])
        fs = C.check_text("山田太郎は架空村に住む。\n```\n山田太郎\n```\n[要追加：山田太郎]", rules)
        strong = cats(fs, "伏せ字")
        # 伏せ字は漏れ防止のため、コードブロックや [要追加] の中も含めて全体を見る
        self.assertEqual([(f.line, f.match) for f in strong], [(1, "山田太郎"), (1, "架空村"), (3, "山田太郎"),
                                                              (5, "山田太郎")])
        self.assertTrue(all(f.severity == "strong" for f in strong))

    def test_weekday(self):
        fs = self.check("2024年1月1日（火）に面会した。")
        wd = cats(fs, "曜日")
        self.assertEqual(len(wd), 1)
        self.assertIn("月曜日", wd[0].message)
        self.assertEqual(wd[0].match, "2024年1月1日（火）")
        for ok in ("2024年1月1日（月）", "2024年1月1日(月曜)", "2024年1月1日(月曜日)", "令和6年1月1日（月）",
                   "2024/1/1(月)"):
            self.assertEqual(cats(self.check(ok), "曜日"), [], ok)
        self.assertEqual(len(cats(self.check("2023年2月30日（木）"), "曜日")), 1)

    def test_positions_and_order(self):
        text = "一行目。\n\n  二行目で相談した。\n三行目で相談した。"
        fs = self.check(text, name="前編")
        lines = text.split("\n")
        self.assertEqual([f.line for f in fs], sorted(f.line for f in fs))
        for f in fs:
            self.assertEqual(f.doc, "前編")
            if f.start >= 0:
                self.assertEqual(lines[f.line - 1][f.start:f.end], f.match)
            self.assertLessEqual(len(f.excerpt), 82)

    def test_excerpt_is_short(self):
        text = "あ" * 200 + "相談" + "い" * 200
        f = cats(self.check(text), "禁句")[0]
        self.assertIn("相談", f.excerpt)
        self.assertLessEqual(len(f.excerpt), 82)

    def test_summarize(self):
        fs = self.check("相談した。あなたも気をつけてください。")
        s = C.summarize(fs)
        self.assertEqual(s["total"], len(fs))
        self.assertEqual(s["by_category"], {"禁句": 1, "助言表現": 2})
        self.assertEqual(sum(s["by_severity"].values()), len(fs))


class CheckSeriesTest(ComplianceTestBase):
    def test_birth_year_conflict(self):
        docs = [("前編", "はじめに。\n2015年、私は30歳だった。"), ("後編", "令和2年、当時40歳。")]
        fs = C.check_series(docs, self.rules)
        sc = cats(fs, "シリーズ矛盾")
        self.assertEqual([(f.doc, f.line) for f in sc], [("前編", 2), ("後編", 1)])
        for f in sc:
            self.assertIn("前編 2行目", f.message)
            self.assertIn("後編 1行目", f.message)
            self.assertIn("1985", f.message)
            self.assertIn("1980", f.message)
        self.assertEqual(sc[0].match, "2015年、私は30歳")

    def test_birth_year_consistent(self):
        docs = [("前編", "2015年、私は30歳だった。"), ("後編", "2020年に35歳。1986年生まれの私。"),
                ("番外", "2016年、妻は25歳、私は31歳。")]
        self.assertEqual(cats(C.check_series(docs, self.rules), "シリーズ矛盾"), [])

    def test_weekday_in_series(self):
        docs = [("前編", "2024年1月1日（火）。"), ("後編", "問題なし。")]
        fs = C.check_series(docs, self.rules)
        self.assertEqual([(f.category, f.doc) for f in fs], [("曜日", "前編")])

    def test_series_does_not_modify_docs(self):
        docs = [("前編", "2015年、私は30歳だった。"), ("後編", "令和2年、当時40歳。")]
        before = [tuple(d) for d in docs]
        C.check_series(docs, self.rules)
        self.assertEqual(docs, before)


if __name__ == "__main__":
    unittest.main()
