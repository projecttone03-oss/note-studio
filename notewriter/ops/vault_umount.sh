#!/usr/bin/env bash
# 【未検証】暗号化フォルダを外す（notewriter・壁打ちセッションを止めてから）。
set -euo pipefail
. "$(dirname "$0")/common.sh"

if tmux ls 2>/dev/null | grep -q '^nwk-'; then
  die "壁打ちセッション（tmux の nwk-…）が動いています。./nw kabeuchi stop 記事名 で止めてから外してください。"
fi
systemctl --user stop nw-web.service 2>/dev/null || true
fusermount -u "$NW_PLAIN"
say "外しました。"
