#!/usr/bin/env bash
# 【未検証】復元テスト: 最新のバックアップを一時フォルダに戻し、読み取り専用でマウントして中身を確かめる。
# 本番の $NW_CIPHER・$NW_PLAIN には触らない。終わったら一時フォルダを消す。
set -euo pipefail
. "$(dirname "$0")/common.sh"

[ -n "$RESTIC_REPOSITORY" ] || die "RESTIC_REPOSITORY を設定してください。"
export RESTIC_REPOSITORY RESTIC_PASSWORD_FILE
WORK="$(mktemp -d "$HOME/.nw-restore-test.XXXXXX")"
chmod 700 "$WORK"
cleanup() { fusermount -u "$WORK/plain" 2>/dev/null || true; rm -rf "$WORK"; }
trap cleanup EXIT

restic restore latest --tag notewriter --target "$WORK/restored"
CIPHER_COPY="$WORK/restored$NW_CIPHER"
[ -f "$CIPHER_COPY/gocryptfs.conf" ] || die "復元したものに gocryptfs.conf がありません。"
mkdir -p "$WORK/plain"
say "復元したものをマウントします（gocryptfs のパスワードを入力）。"
gocryptfs -ro "$CIPHER_COPY" "$WORK/plain"
[ -f "$WORK/plain/$NW_MARK" ] || die "目印 $NW_MARK が見えません。復元に失敗しています。"
n_restored="$(find "$WORK/plain" -type f | wc -l)"
say "復元できました: ファイル $n_restored 個（目印あり）。"
if is_mounted; then
  n_live="$(find "$NW_PLAIN" -type f | wc -l)"
  say "参考: いまの作業フォルダはファイル $n_live 個（バックアップ後に増えた分は差が出ます）。"
fi
date -Iseconds > "$HOME/.config/notewriter/last-restore-test" 2>/dev/null || true
say "復元テスト OK。一時フォルダは消します。"
