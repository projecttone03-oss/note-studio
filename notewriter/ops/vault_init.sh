#!/usr/bin/env bash
# 【未検証】暗号化フォルダ（gocryptfs）を最初に1回だけ作る。
# - パスワードはこの画面で入力するだけで、ファイルには保存しない（再起動のたびに ./vault_mount.sh で入力）。
# - 表示されるマスターキーは紙に書いて保管（パスワードを忘れたときの復元用）。ファイルやチャットに貼らない。
# - Claude Code のセッション履歴（~/.claude/projects/）も暗号化フォルダの中に置く（シンボリックリンク）。
set -euo pipefail
. "$(dirname "$0")/common.sh"

command -v gocryptfs >/dev/null || die "gocryptfs がありません（./setup_vps.sh）。"
[ -e "$NW_CIPHER/gocryptfs.conf" ] && die "$NW_CIPHER はもう初期化されています。"
confirm "$NW_CIPHER に暗号化フォルダを作ります。"

mkdir -p "$NW_CIPHER" "$NW_PLAIN"
chmod 700 "$NW_CIPHER" "$NW_PLAIN"
gocryptfs -init "$NW_CIPHER"
say "↑ マスターキーを紙に書き写してください（画面を閉じると二度と表示されません）。"

gocryptfs "$NW_CIPHER" "$NW_PLAIN"
mkdir -p "$NW_PLAIN/claude-projects" "$NW_PLAIN/tmp"
chmod 700 "$NW_PLAIN/claude-projects" "$NW_PLAIN/tmp"
NW_DATA_DIR="$NW_PLAIN" "$NW_REPO/nw" init

# ~/.claude/projects を暗号化フォルダの中へ（すでにあれば中身を移してから置き換える）
mkdir -p "$HOME/.claude"
if [ -d "$HOME/.claude/projects" ] && [ ! -L "$HOME/.claude/projects" ]; then
  cp -a "$HOME/.claude/projects/." "$NW_PLAIN/claude-projects/"
  rm -rf "$HOME/.claude/projects"
fi
ln -sfn "$NW_PLAIN/claude-projects" "$HOME/.claude/projects"
say "~/.claude/projects → $NW_PLAIN/claude-projects（マウントしていないときはリンク先が見えず、書き込めません）。"
say "完了。restic のパスワードファイルを作ってから ./backup.sh を試してください。"
