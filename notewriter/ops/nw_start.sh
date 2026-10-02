#!/usr/bin/env bash
# 【未検証】notewriter の画面を Tailscale のアドレスだけで起動する。暗号化フォルダがマウントされていなければ起動しない。
# - 一時ファイル（TMPDIR）も暗号化フォルダの中に置く。
# - systemd（nw-web.service）からも、手でも使える。
set -euo pipefail
. "$(dirname "$0")/common.sh"

require_mounted
[ -L "$HOME/.claude/projects" ] || die "~/.claude/projects が暗号化フォルダへのリンクになっていません（./vault_init.sh を参照）。"
TS_IP="$(tailscale ip -4 2>/dev/null | head -n1)"
[ -n "$TS_IP" ] || die "Tailscale のアドレスが分かりません（sudo tailscale up）。"

export NW_DATA_DIR="$NW_PLAIN"
export TMPDIR="$NW_PLAIN/tmp"
umask 077
cd "$NW_REPO"
exec ./nw serve --host "$TS_IP"
