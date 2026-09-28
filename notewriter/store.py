"""ファイル保存の土台: 作業フォルダの場所、暗号化マウントの確認、原子的な書き込み、front matter。

作業フォルダ（既定: リポジトリ直下の workspace/、環境変数 NW_DATA_DIR で変更）には個人情報が入るので
.gitignore で除外している。本番では gocryptfs の復号ビュー（マウント先）を NW_DATA_DIR に指定する。

マウント確認の仕組み: `./nw init` で作業フォルダの中に目印ファイル .nw-vault を作る。
gocryptfs の中に作った目印はマウント中にしか見えないので、目印がない＝未マウント（同名の平文フォルダ）
とみなして読み書きを拒否する。これで平文フォルダへの誤書き込みを防ぐ。
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
VAULT_MARK = ".nw-vault"


class VaultError(RuntimeError):
    """作業フォルダが使えない（未初期化・未マウント）。"""


def data_dir() -> Path:
    return Path(os.environ.get("NW_DATA_DIR") or ROOT / "workspace")


def vault() -> Path:
    """確認済みの作業フォルダを返す。目印がなければ VaultError。"""
    d = data_dir()
    if not d.is_dir():
        raise VaultError(f"作業フォルダ {d} がありません。初回は ./nw init を実行してください"
                         "（本番では gocryptfs をマウントしてから）。")
    if not (d / VAULT_MARK).is_file():
        raise VaultError(f"作業フォルダ {d} に目印 {VAULT_MARK} がありません。暗号化フォルダがマウントされていない"
                         "可能性があります。平文フォルダへの誤書き込みを防ぐため、処理を止めました。")
    return d


def init_vault() -> Path:
    d = data_dir()
    d.mkdir(parents=True, exist_ok=True)
    mark = d / VAULT_MARK
    if not mark.exists():
        atomic_write(mark, f"created: {now()}\n")
    return d


def now() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def atomic_write(path: Path, text: str) -> None:
    """一時ファイル（同じフォルダ内＝同じ暗号化境界の内側）→ fsync → rename → フォルダも fsync。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    try:
        dfd = os.open(str(path.parent), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dfd)
    except OSError:
        pass
    finally:
        os.close(dfd)


def write_json(path: Path, data) -> None:
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


# ---- front matter（YAML風の最小サブセット: 1行1項目「キー: 値」、リストはカンマ区切り） ----

def _one_line(v: str) -> str:
    return re.sub(r"[\r\n]+", " ", str(v)).strip()


def dump_md(meta: Dict[str, object], body: str) -> str:
    lines = ["---"]
    for k, v in meta.items():
        if isinstance(v, (list, tuple)):
            v = ", ".join(_one_line(x).replace(",", "，") for x in v if _one_line(x))
        lines.append(f"{k}: {_one_line('' if v is None else v)}")
    lines.append("---")
    return "\n".join(lines) + "\n" + body.rstrip("\n") + "\n"


def parse_md(text: str, list_keys: Tuple[str, ...] = ()) -> Tuple[Dict[str, object], str]:
    meta: Dict[str, object] = {}
    body = text
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end == -1 and text.endswith("\n---"):
            end = len(text) - 4
        if end != -1:
            for line in text[4:end].splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
            body = text[end + 5:]
    for k in list_keys:
        raw = meta.get(k, "")
        meta[k] = split_list(raw if isinstance(raw, str) else "")
    return meta, body


def split_list(raw: str) -> List[str]:
    return [x.strip() for x in re.split(r"[,、，]", raw or "") if x.strip()]


def next_id(folder: Path, prefix: str, width: int = 4) -> str:
    """フォルダ内の既存ファイル（prefix+数字.md）から次の番号を決める。"""
    n = 0
    if folder.is_dir():
        for p in folder.glob(f"{prefix}*.md"):
            m = re.fullmatch(rf"{prefix}(\d+)", p.stem)
            if m:
                n = max(n, int(m[1]))
    return f"{prefix}{n + 1:0{width}d}"
