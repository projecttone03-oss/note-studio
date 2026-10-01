#!/usr/bin/env bash
# 【未検証】暗号化フォルダをマウントする（再起動のあと毎回。パスワードを入力）。
set -euo pipefail
. "$(dirname "$0")/common.sh"

if is_mounted; then say "もうマウントされています: $NW_PLAIN"; exit 0; fi
[ -e "$NW_CIPHER/gocryptfs.conf" ] || die "$NW_CIPHER が初期化されていません（./vault_init.sh）。"
# 同名の平文フォルダに何か書かれていたら止める（マウント前に誤って書き込まれた可能性）
if [ -d "$NW_PLAIN" ] && [ -n "$(ls -A "$NW_PLAIN" 2>/dev/null)" ]; then
  die "マウント前の $NW_PLAIN にファイルがあります（平文で書き込まれた可能性）。中身を確かめ、安全に消してからやり直してください。"
fi
mkdir -p "$NW_PLAIN"
gocryptfs "$NW_CIPHER" "$NW_PLAIN"
is_mounted || die "マウントしましたが目印 $NW_MARK が見えません。"
say "マウントしました: $NW_PLAIN"
