#!/usr/bin/env bash
# notewriter VPS 用スクリプトの共通設定。【未検証】bash -n（文法チェック）だけ通しています。実機で一度ずつ確かめてください。
# 値は環境変数か ~/.config/notewriter/ops.env で上書きできます。
set -euo pipefail

OPS_ENV="${OPS_ENV:-$HOME/.config/notewriter/ops.env}"
# shellcheck disable=SC1090
[ -f "$OPS_ENV" ] && . "$OPS_ENV"

NW_REPO="${NW_REPO:-$HOME/note-studio}"          # リポジトリ（プログラム。秘密は入れない）
NW_CIPHER="${NW_CIPHER:-$HOME/nw-cipher}"        # gocryptfs の暗号化された中身（バックアップするのはこちら）
NW_PLAIN="${NW_PLAIN:-$HOME/nw-data}"            # 復号ビュー（マウント先）。notewriter の作業フォルダ NW_DATA_DIR
NW_MARK=".nw-vault"                               # ./nw init が作る目印（マウント中だけ見える）
RESTIC_REPOSITORY="${RESTIC_REPOSITORY:-}"        # 例: sftp:backup@backup-host:/srv/restic/nw  または s3:...
RESTIC_PASSWORD_FILE="${RESTIC_PASSWORD_FILE:-$HOME/.config/notewriter/restic-pass}"

say()  { printf '%s\n' "$*"; }
die()  { printf 'エラー: %s\n' "$*" >&2; exit 1; }

is_mounted() {
  mountpoint -q "$NW_PLAIN" && [ -f "$NW_PLAIN/$NW_MARK" ]
}

require_mounted() {
  is_mounted || die "暗号化フォルダ $NW_PLAIN がマウントされていません（./vault_mount.sh を先に）。平文への誤書き込みを防ぐため止めます。"
}

confirm() {
  local ans
  read -r -p "$1 [yes と入力で続行] " ans
  [ "$ans" = "yes" ] || die "やめました。"
}
