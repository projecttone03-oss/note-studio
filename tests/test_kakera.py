"""notewriter のかけら管理（SPEC.md 機能1）を確認する。

実行: python3 -m unittest discover tests
"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from notewriter import kakera, store


class KakeraTest(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("NW_DATA_DIR")
        self.tmp = tempfile.mkdtemp(prefix="notewriter-test-")
        os.environ["NW_DATA_DIR"] = self.tmp
        store.init_vault()

    def tearDown(self):
        if self._old is None:
            os.environ.pop("NW_DATA_DIR", None)
        else:
            os.environ["NW_DATA_DIR"] = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_create_get_list(self):
        k = kakera.create_kakera("手が震えた。\n\n二段落目", article="前編", section="区間1",
                                 viewpoints=["体の反応", "感情"], tags="朝、玄関")
        self.assertEqual(k["id"], "K0001")
        got = kakera.get_kakera("K0001")
        self.assertEqual(got["body"], "手が震えた。\n\n二段落目")
        self.assertEqual(got["viewpoints"], ["体の反応", "感情"])
        self.assertEqual(got["tags"], ["朝", "玄関"])
        self.assertEqual(got["status"], "未使用")
        self.assertEqual(got["article"], "前編")
        self.assertTrue(Path(got["path"]).is_file())
        kakera.create_kakera("二つ目")
        self.assertEqual([x["id"] for x in kakera.list_kakera()], ["K0001", "K0002"])
        with self.assertRaises(KeyError):
            kakera.get_kakera("K0099")
        with self.assertRaises(KeyError):
            kakera.get_kakera("../.nw-vault")
        with self.assertRaises(ValueError):
            kakera.create_kakera("x", status="下書き")

    def test_search_multiple_conditions(self):
        kakera.create_kakera("取調室は寒かった", article="前編", section="区間1", viewpoints=["五感"], tags=["警察"])
        kakera.create_kakera("刑事が「座って」と言った", article="前編", section="区間1",
                             viewpoints=["セリフ"], tags=["警察"], status="使用済み")
        kakera.create_kakera("寒い朝に家を出た", article="後編", section="区間2", viewpoints=["五感"])
        self.assertEqual(len(kakera.search_kakera(q="寒")), 2)
        self.assertEqual([k["id"] for k in kakera.search_kakera(q="寒", article="前編")], ["K0001"])
        self.assertEqual([k["id"] for k in kakera.search_kakera(tag="警察", status="使用済み")], ["K0002"])
        self.assertEqual([k["id"] for k in kakera.search_kakera(viewpoint="五感", section="区間2")], ["K0003"])
        # q は空白区切りでAND、タグ・区間・記事名にも当たる
        self.assertEqual([k["id"] for k in kakera.search_kakera(q="警察 座って")], ["K0002"])
        self.assertEqual(len(kakera.search_kakera(q="後編")), 1)
        self.assertEqual(kakera.search_kakera(q="寒　存在しない語"), [])

    def test_update(self):
        k = kakera.create_kakera("元の本文", article="前編")
        u = kakera.update_kakera(k["id"], body="新しい本文", status="保留", viewpoints="セリフ, 分岐点", id="K9999")
        self.assertEqual(u["id"], k["id"])
        got = kakera.get_kakera(k["id"])
        self.assertEqual(got["body"], "新しい本文")
        self.assertEqual(got["status"], "保留")
        self.assertEqual(got["viewpoints"], ["セリフ", "分岐点"])
        self.assertEqual(got["article"], "前編")
        self.assertEqual(got["created"], k["created"])
        with self.assertRaises(ValueError):
            kakera.update_kakera(k["id"], status="不明")
        with self.assertRaises(ValueError):
            kakera.update_kakera(k["id"], nosuch="x")
        with self.assertRaises(KeyError):
            kakera.update_kakera("K0404", body="x")

    def test_coverage(self):
        kakera.save_article("前編", series="事件体験談", order=1, sections=["区間1", "区間2"])
        kakera.create_kakera("a", article="前編", section="区間2", viewpoints=["五感", "セリフ"])
        kakera.create_kakera("b", article="前編", section="区間2", viewpoints=["五感"])
        kakera.create_kakera("c", article="前編", section="区間3", viewpoints=["分岐点"])
        kakera.create_kakera("d", article="前編")
        kakera.create_kakera("e", article="後編", section="区間1", viewpoints=["五感"])
        cov = kakera.coverage("前編")
        self.assertEqual(cov["viewpoints"], ["五感", "体の反応", "セリフ", "分岐点", "感情"])
        self.assertEqual([s["section"] for s in cov["sections"]], ["区間1", "区間2", "区間3"])
        s1, s2, s3 = cov["sections"]
        self.assertEqual(s1["total"], 0)
        self.assertEqual(s1["filled"], 0)
        self.assertEqual(len(s1["missing"]), 5)
        self.assertEqual(s2["counts"]["五感"], 2)
        self.assertEqual(s2["counts"]["セリフ"], 1)
        self.assertEqual(s2["total"], 2)
        self.assertEqual(s2["filled"], 2)
        self.assertEqual(s2["missing"], ["体の反応", "分岐点", "感情"])
        self.assertEqual(s3["counts"]["分岐点"], 1)
        self.assertEqual(cov["unsectioned"], 1)

    def test_vault_config_overrides(self):
        store.write_json(Path(self.tmp) / "config" / "kakera.json", {"statuses": ["未使用", "ボツ"]})
        self.assertEqual(kakera.load_config()["statuses"], ["未使用", "ボツ"])
        self.assertIn("五感", kakera.load_config()["coverage_viewpoints"])
        kakera.create_kakera("x", status="ボツ")
        with self.assertRaises(ValueError):
            kakera.create_kakera("y", status="保留")

    def test_articles(self):
        kakera.save_article("後編", series="事件体験談", order=2, sections=["区間1"])
        kakera.save_article("前編", series="事件体験談", order=1, sections="区間1\n区間2")
        kakera.save_article("前編", series="事件体験談", order=1, sections=["区間A"])
        arts = kakera.list_articles()
        self.assertEqual([a["name"] for a in arts], ["前編", "後編"])
        self.assertEqual(arts[0]["sections"], ["区間A"])
        kakera.delete_article("後編")
        self.assertEqual([a["name"] for a in kakera.list_articles()], ["前編"])
        with self.assertRaises(KeyError):
            kakera.delete_article("後編")

    def test_neta_promote(self):
        n = kakera.create_neta("パチンコ店の音がうるさかった", tags=["パチンコ"])
        self.assertEqual(n["status"], "未整理")
        self.assertEqual(kakera.stats()["neta_unsorted"], 1)
        k = kakera.promote_neta(n["id"], article="パチンコ", section="区間1", viewpoints=["五感"], tags=["音"])
        self.assertEqual(k["source"], n["id"])
        self.assertEqual(k["body"], "パチンコ店の音がうるさかった")
        self.assertEqual(k["tags"], ["パチンコ", "音"])
        n2 = kakera.get_neta(n["id"])
        self.assertEqual(n2["status"], "かけら化済み")
        self.assertEqual(n2["kakera_id"], k["id"])
        with self.assertRaises(ValueError):
            kakera.promote_neta(n["id"])
        st = kakera.stats()
        self.assertEqual(st["neta_unsorted"], 0)
        self.assertEqual(st["kakera_total"], 1)
        self.assertEqual(st["by_article"]["パチンコ"], 1)
        self.assertEqual(st["by_status"]["未使用"], 1)
        kakera.update_neta(n["id"], body="書き直し")
        self.assertEqual(kakera.get_neta(n["id"])["body"], "書き直し")
        kakera.delete_neta(n["id"])
        self.assertEqual(kakera.list_neta(), [])

    def test_delete_with_provenance(self):
        k1 = kakera.create_kakera("根拠になったかけら", article="前編")
        k2 = kakera.create_kakera("未参照", article="前編")
        with self.assertRaises(ValueError):
            kakera.record_provenance("前編", "P003", [k1["id"], "K0999"])
        self.assertEqual(kakera.list_provenance(), [])
        rec = kakera.record_provenance("前編", "P003", [k1["id"]])
        self.assertEqual(rec["id"], "G0001")
        self.assertEqual([r["id"] for r in kakera.delete_warnings(k1["id"])], ["G0001"])
        self.assertEqual(kakera.delete_warnings(k2["id"]), [])
        with self.assertRaises(kakera.ReferencedError) as cm:
            kakera.delete_kakera(k1["id"])
        self.assertEqual([r["id"] for r in cm.exception.records], ["G0001"])
        self.assertTrue(Path(k1["path"]).is_file(), "force なしでは消えない")
        kakera.delete_kakera(k2["id"])
        kakera.delete_kakera(k1["id"], force=True)
        self.assertEqual(kakera.list_kakera(), [])
        recs = kakera.list_provenance()
        self.assertEqual(len(recs), 1, "来歴レコードは残る")
        self.assertEqual(recs[0]["kakera_ids"], [k1["id"]])
        self.assertEqual(recs[0]["missing_kakera_ids"], [k1["id"]])

    def test_ids_not_reused_after_delete(self):
        kakera.create_kakera("1")
        k2 = kakera.create_kakera("2")
        kakera.delete_kakera(k2["id"])
        self.assertEqual(kakera.create_kakera("3")["id"], "K0003")
        n = kakera.create_neta("n")
        kakera.delete_neta(n["id"])
        self.assertEqual(kakera.create_neta("n2")["id"], "N0002")
        # counters.json が消えても既存ファイルの最大番号より小さくはならない
        (Path(self.tmp) / "counters.json").unlink()
        self.assertEqual(kakera.create_kakera("4")["id"], "K0004")

    def test_uninitialized_vault(self):
        plain = tempfile.mkdtemp(prefix="notewriter-plain-")
        self.addCleanup(shutil.rmtree, plain, True)
        os.environ["NW_DATA_DIR"] = plain  # 目印 .nw-vault のない平文フォルダ
        with self.assertRaises(store.VaultError):
            kakera.list_kakera()
        with self.assertRaises(store.VaultError):
            kakera.create_kakera("書き込まれてはいけない")
        self.assertEqual(os.listdir(plain), [], "未マウント時は何も書かない")
        os.environ["NW_DATA_DIR"] = os.path.join(plain, "nothing")
        with self.assertRaises(store.VaultError):
            kakera.create_neta("x")
        # 設定の読み込みはリポジトリの既定値で動く
        self.assertIn("未使用", kakera.load_config()["statuses"])

    def test_no_tmp_files_left(self):
        k = kakera.create_kakera("a", article="前編")
        kakera.update_kakera(k["id"], body="b")
        kakera.save_article("前編", sections=["区間1"])
        kakera.record_provenance("前編", "P1", [k["id"]])
        n = kakera.create_neta("n")
        kakera.promote_neta(n["id"])
        leftovers = [str(p) for p in Path(self.tmp).rglob("*") if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
