"""画面テストの共通部品: 作業フォルダを用意したテストに、Web サーバーを足す。"""
import threading
import urllib.error
import urllib.parse
import urllib.request

from notewriter import store, web
from tests.test_writing_web import OPENER


class ServerMixin:
    def start_server(self):
        store.write_json(store.vault() / web.NOTICE_FILE, {"acknowledged_at": store.now(),
                                                           "notice_version": web.NOTICE_VERSION})
        self.httpd = web.make_server("127.0.0.1", 0)
        self.httpd.quiet = True
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def req(self, path, fields=None, files=None):
        """fields があれば POST（files は [(フィールド名, ファイル名, bytes)] で multipart）。"""
        headers = {"Sec-Fetch-Site": "same-origin"}
        data = None
        if files is not None:
            boundary = "----nwtestboundary"
            parts = []
            for k, v in (fields or {}).items():
                parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode("utf-8"))
            for name, fn, blob in files:
                parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{fn}"\r\n'
                             f'Content-Type: application/octet-stream\r\n\r\n'.encode("utf-8") + blob + b"\r\n")
            parts.append(f"--{boundary}--\r\n".encode("utf-8"))
            data = b"".join(parts)
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        elif fields is not None:
            data = urllib.parse.urlencode(fields, doseq=True).encode("utf-8")
        r = urllib.request.Request(self.base + path, data=data, method="POST" if data is not None else "GET",
                                   headers=headers)
        try:
            with OPENER.open(r) as resp:
                return resp.status, resp.read().decode("utf-8", "replace"), resp.headers
        except urllib.error.HTTPError as ex:
            return ex.code, ex.read().decode("utf-8", "replace"), ex.headers
