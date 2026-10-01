"""VPS 用スクリプト（notewriter/ops/）の文法チェック。実行はしない（bash -n だけ）。"""
import shutil
import subprocess
import unittest
from pathlib import Path

OPS = Path(__file__).resolve().parent.parent / "notewriter" / "ops"


class OpsSyntaxTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("bash"), "bash がない環境")
    def test_bash_n(self):
        scripts = sorted(OPS.glob("*.sh"))
        self.assertGreaterEqual(len(scripts), 8)
        for s in scripts:
            r = subprocess.run(["bash", "-n", str(s)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"{s.name}: {r.stderr}")
            head = s.read_text(encoding="utf-8")[:400]
            self.assertIn("未検証", head, f"{s.name} の先頭に【未検証】の注意がない")

    def test_readme_marks_unverified(self):
        self.assertIn("【未検証】", (OPS / "README.md").read_text(encoding="utf-8"))
