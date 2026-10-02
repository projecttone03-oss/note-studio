#!/usr/bin/env bash
# 【未検証】外部への暗号化バックアップ。
# - バックアップするのは「暗号化された中身」（$NW_CIPHER）。gocryptfs の暗号の上に、restic の暗号をもう1枚重ねる。
# - restic のパスワードは $RESTIC_PASSWORD_FILE（chmod 600）。gocryptfs のパスワード・マスターキーとは別にし、紙でも保管する。
# - gocryptfs.conf（鍵の入れ物）も一緒に入る。パスワードかマスターキーがなければ復元できない。
# 使い方: RESTIC_REPOSITORY=sftp:user@host:/srv/restic/nw ./backup.sh [--init]
set -euo pipefail
. "$(dirname "$0")/common.sh"

[ -n "$RESTIC_REPOSITORY" ] || die "RESTIC_REPOSITORY を設定してください（~/.config/notewriter/ops.env）。"
[ -f "$RESTIC_PASSWORD_FILE" ] || die "$RESTIC_PASSWORD_FILE がありません（chmod 600 で作る）。"
[ "$(stat -c %a "$RESTIC_PASSWORD_FILE")" = "600" ] || die "$RESTIC_PASSWORD_FILE の権限を 600 にしてください。"
export RESTIC_REPOSITORY RESTIC_PASSWORD_FILE

if [ "${1:-}" = "--init" ]; then
  restic init
fi
restic backup "$NW_CIPHER" --tag notewriter --exclude-caches
restic forget --tag notewriter --keep-daily 7 --keep-weekly 8 --keep-monthly 12 --prune
restic check --read-data-subset=5%
say "バックアップしました。月に1回は ./restore_test.sh で復元できることを確かめてください。"
