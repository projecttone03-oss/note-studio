"""参考書籍（books・web_books）の確認。書籍は使わず、テスト内で作った小さな PDF とテキストだけを使う。"""
import json
import os
import re
import shutil
import time
import unittest
import urllib.parse

from notewriter import books, style_learn, writing
from tests.test_writing import WritingBase
from tests.webutil import ServerMixin


def make_pdf(pages):
    """ASCII の文字だけの小さな PDF（pdftotext で読める最小限の形）。"""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", None, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for text in pages:
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream.decode('latin-1')}\nendstream")
        content_no = len(objs)
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents {content_no} 0 R >>")
        kids.append(len(objs))
    objs[1] = "<< /Type /Pages /Kids [" + " ".join(f"{k} 0 R" for k in kids) + f"] /Count {len(kids)} >>"
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode("latin-1")
    out += "".join(f"{x:010d} 00000 n \n" for x in offsets).encode("latin-1")
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("latin-1")
    return out


BOOK_TXT = ("第1章 書き出し\n最初の一文で読者の疑問を言葉にし、答えは段落の最後に置くと読み進めてもらいやすい。\f"
            "第2章 会話\n会話文は一つの段落に一人分だけ入れ、話し手が変わったら改行する。")


class BookTest(WritingBase):
    ENV = WritingBase.ENV + ("NW_PDFTOOL", "NW_PDFTOTEXT")

    def cands(self, *rows):
        return json.dumps({"candidates": [{"rule": r, "reason": "読みやすさ", "page": p} for r, p in rows]}, ensure_ascii=False)

    def test_txt_and_analysis_with_copy_check(self):
        b = books.add_book("文章術.txt", BOOK_TXT.encode("utf-8"), "ダミーの文章術")
        self.assertEqual((b["id"], b["pages"]), ("B0001", 2))
        self.assertIn("=== p.2 ===", books.page_text("B0001", 2, 2))
        self.assertEqual(books.find("B0001", ["会話文"])[0][0], 2)
        self.set_result(self.cands(("段落の冒頭で読者の疑問を示し、答えを段落の終わりに置く", 1),
                                   ("会話文は一つの段落に一人分だけ入れ、話し手が変わったら改行する", 2)))
        p = books.prepare("B0001", 1, 2, "書き出し")
        job = books.run("B0001", 1, 2, "書き出し", p["confirm_token"])
        self.assertEqual(job["status"], "done", job.get("error"))
        call = self.last_call()
        self.assertIn("最初の一文で読者の疑問", call["stdin"], "アプリが取り出した文字を標準入力で渡す")
        self.assertEqual(call["allowed"], "", "ツールなし")
        self.assertNotIn("文章術", " ".join(call["argv"]))
        items = {c["rule"][:4]: c for c in style_learn.list_candidates("未検討")}
        self.assertFalse(items["段落の冒"]["flags"])
        self.assertTrue(items["会話文は"]["flags"], "本文と長く同じ文が続く候補には印")
        self.assertEqual(items["会話文は"]["source"], "book:B0001")
        self.assertEqual(items["会話文は"]["refs"], ["p.2"])
        before = writing.get_style()
        with self.assertRaises(ValueError):
            style_learn.adopt(items["会話文は"]["id"])
        self.assertEqual(writing.get_style(), before, "印のある候補は直すまで入らない")
        style_learn.adopt(items["会話文は"]["id"], "話し手が替わるたびに段落を分ける")
        self.assertIn("話し手が替わるたびに段落を分ける", writing.get_style())

    def test_page_range_and_limits(self):
        books.add_book("a.txt", BOOK_TXT.encode("utf-8"))
        with self.assertRaises(ValueError):
            books.prepare("B0001", 2, 5)
        with self.assertRaises(ValueError):
            books.add_book("a.docx", b"x")
        with self.assertRaises(ValueError):
            books.add_book("a.pdf", b"not a pdf")
        self.assertEqual([b["id"] for b in books.list_books()], ["B0001"], "失敗した取り込みは残さない")
        books.delete_book("B0001")
        self.assertEqual(books.list_books(), [])

    def test_no_extractor_message(self):
        os.environ["NW_PDFTOOL"] = "/nonexistent"
        os.environ["NW_PDFTOTEXT"] = ""
        old = shutil.which
        try:
            shutil.which = lambda *a, **k: None
            with self.assertRaises(ValueError) as cm:
                books.add_book("a.pdf", make_pdf(["Hello"]))
        finally:
            shutil.which = old
        self.assertIn("poppler-utils", str(cm.exception))

    @unittest.skipUnless(shutil.which("pdftotext"), "pdftotext がない環境")
    def test_pdf_with_pdftotext(self):
        os.environ["NW_PDFTOOL"] = "/nonexistent"
        b = books.add_book("book.pdf", make_pdf(["Show the scene first.", "Then explain the meaning."]), "Dummy")
        self.assertEqual(b["pages"], 2)
        self.assertIn("Then explain", books.page_text(b["id"], 2, 2))


class BookWebTest(ServerMixin, WritingBase):
    def test_flow(self):
        self.start_server()
        status, body, _ = self.req("/books")
        self.assertIn("書籍を取り込む", body)
        status, _, headers = self.req("/books/upload", {"title": "ダミー"}, files=[("file", "a.txt", BOOK_TXT.encode("utf-8"))])
        self.assertEqual(status, 303, headers)
        status, body, _ = self.req("/books/B0001?q=" + urllib.parse.quote("会話文"))
        self.assertIn("p.2", body)
        self.set_result(json.dumps({"candidates": [{"rule": "結論を先に書く", "reason": "x", "page": 1}]}, ensure_ascii=False))
        status, body, _ = self.req("/books/B0001/confirm", {"start": "1", "end": "2", "focus": ""})
        token = re.search(r'name="confirm_token" value="([0-9a-f]+)"', body)[1]
        status, _, headers = self.req("/books/B0001/run", {"start": "1", "end": "2", "focus": "", "confirm_token": token})
        jid = re.search(r"/books/jobs/(BJ\d+)", headers["Location"])[1]
        for _ in range(100):
            if books.JOB.get(jid)["status"] != "running":
                break
            time.sleep(0.1)
        status, body, _ = self.req("/style/candidates")
        self.assertIn("結論を先に書く", body)
        self.assertIn("参考書籍 B0001", body)
