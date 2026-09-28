"""Claude Code の PreToolUse フック（notewriter の claude_runner から --settings で登録する単体スクリプト）。

許可するツール名は環境変数 NW_ALLOWED_TOOLS（カンマ区切り。空 = すべて拒否）で受け取る。
標準入力の JSON の tool_name が許可リストにあれば終了コード 0、それ以外は stderr に理由を書いて 2（拒否）。
JSON が読めないときも拒否する。フラグ（--tools / --allowedTools）と二重に止めるための仕組み。
"""
import json
import os
import sys


def main() -> int:
    allowed = {t.strip() for t in (os.environ.get("NW_ALLOWED_TOOLS") or "").split(",") if t.strip()}
    try:
        data = json.loads(sys.stdin.read() or "")
        name = str(data.get("tool_name") or "")
    except Exception:
        sys.stderr.write("notewriter: フックの入力を読めなかったため、ツールの使用を拒否しました。\n")
        return 2
    if name and name in allowed:
        return 0
    if allowed:
        sys.stderr.write("notewriter: このセッションで使えるツールは " + ", ".join(sorted(allowed))
                         + " だけです（" + (name or "不明なツール") + " は拒否）。回答本文だけで返してください。\n")
    else:
        sys.stderr.write("notewriter: このセッションではツールを使えません（" + (name or "不明なツール")
                         + " は拒否）。回答本文だけで返してください。\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
