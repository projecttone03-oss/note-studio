"""APIキー（Perplexity）の保存・読み出し・伏せ字化。

保存先: 作業フォルダ（store.data_dir()）の secrets/perplexity_api_key
- フォルダは 0700、ファイルは 0600（本人だけが読める）。作業フォルダ自体も .gitignore 済み。
- 環境変数 PERPLEXITY_API_KEY があればそちらを優先する（VPS 用）。

キーは画面・ログ・エラー・コミットに出さない。表示用に末尾数桁を返すこともしない。
外に出す可能性のある文字列（エラーメッセージ等）は必ず redact() を通す。

※このモジュール名は標準ライブラリの secrets と同じだが、パッケージ内からは
  `from . import secrets` で明示的に読み込むので衝突しない。
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import List

from . import store

ENV_NAME = "PERPLEXITY_API_KEY"
SECRETS_DIR = "secrets"
KEY_FILE = "perplexity_api_key"
MASK = "pplx-****"
_KEY_RX = re.compile(r"pplx-[A-Za-z0-9_\-]+")


def _key_path() -> Path:
    return store.data_dir() / SECRETS_DIR / KEY_FILE


def _read_file_key() -> str:
    try:
        return _key_path().read_text(encoding="utf-8").strip()
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        return ""


def api_key_source() -> str:
    """"env"（環境変数）/ "file"（作業フォルダ）/ ""（未設定）。"""
    if (os.environ.get(ENV_NAME) or "").strip():
        return "env"
    if _read_file_key():
        return "file"
    return ""


def has_api_key() -> bool:
    return api_key_source() != ""


def get_api_key() -> str:
    """内部用。キーそのものを返す（未設定なら ""）。画面やログに出さないこと。"""
    env = (os.environ.get(ENV_NAME) or "").strip()
    if env:
        return env
    return _read_file_key()


def set_api_key(key: str) -> List[str]:
    """キーを保存する。返り値は警告文のリスト（空なら問題なし）。不正な形なら ValueError。"""
    key = (key or "").strip()
    if not key:
        raise ValueError("APIキーが空です。")
    if "\n" in key or "\r" in key:
        raise ValueError("APIキーに改行が含まれています。1行で貼り付けてください。")
    if any(c.isspace() for c in key):
        raise ValueError("APIキーに空白が含まれています。")
    warnings: List[str] = []
    if not key.startswith("pplx-"):
        warnings.append("APIキーが「pplx-」で始まっていません。貼り付け間違いがないか確認してください。")
    if (os.environ.get(ENV_NAME) or "").strip():
        warnings.append(f"環境変数 {ENV_NAME} が設定されているため、実行時はそちらが優先されます。")
    store.vault()  # 未マウントの平文フォルダには書かない
    folder = _key_path().parent
    old_umask = os.umask(0o077)
    try:
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(str(folder), 0o700)
        store.atomic_write(_key_path(), key + "\n")
        os.chmod(str(_key_path()), 0o600)
    finally:
        os.umask(old_umask)
    return warnings


def delete_api_key() -> bool:
    """保存済みのキーファイルを消す。消したら True（環境変数は対象外）。"""
    try:
        _key_path().unlink()
        return True
    except FileNotFoundError:
        return False


def redact(text) -> str:
    """文字列中のキー（保存済み・環境変数・pplx- 形式）を pplx-**** に置き換える。"""
    s = "" if text is None else str(text)
    for k in {get_api_key(), (os.environ.get(ENV_NAME) or "").strip(), _read_file_key()}:
        if k and len(k) >= 4:
            s = s.replace(k, MASK)
    s = _KEY_RX.sub(lambda m: m.group(0) if m.group(0) == MASK else MASK, s)
    return s
