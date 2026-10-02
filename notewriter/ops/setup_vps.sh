#!/usr/bin/env bash
# 【未検証】ConoHa VPS（Ubuntu 24.04）の初期設定。root ではなく sudo できる一般ユーザーで実行する。
# - パッケージ: gocryptfs・restic・tmux・poppler-utils（PDF の文字取り出し）・ufw・unattended-upgrades
# - 外からの接続は Tailscale だけ（ufw で受け付けを閉じ、tailscale0 だけ許可）
# - コアダンプ無効（メモリの中身＝本文がファイルに残らないように）
# 使い方: ./setup_vps.sh   （途中で yes の確認あり）
set -euo pipefail
. "$(dirname "$0")/common.sh"

confirm "パッケージの導入・ファイアウォール・コアダンプ無効化を行います。"

sudo apt-get update
sudo apt-get install -y gocryptfs restic tmux poppler-utils ufw unattended-upgrades python3 git curl
sudo dpkg-reconfigure -f noninteractive unattended-upgrades

# Tailscale（公式の導入スクリプト。中身を確かめてから実行するのが安全）
if ! command -v tailscale >/dev/null 2>&1; then
  say "Tailscale を入れます（https://tailscale.com/install.sh）。"
  curl -fsSL https://tailscale.com/install.sh | sh
fi
say "次に 'sudo tailscale up' を実行し、表示された URL でログインしてください。"

# ファイアウォール: SSH も Tailscale 経由に限る（先に Tailscale で SSH できることを確かめてから有効にすること）
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow in on tailscale0
say "ufw の設定を入れました。Tailscale 経由で SSH できることを確かめてから 'sudo ufw enable' を実行してください。"

# コアダンプ無効
echo '* hard core 0' | sudo tee /etc/security/limits.d/90-nw-nocore.conf >/dev/null
sudo mkdir -p /etc/systemd/coredump.conf.d
printf '[Coredump]\nStorage=none\nProcessSizeMax=0\n' | sudo tee /etc/systemd/coredump.conf.d/90-nw.conf >/dev/null
echo 'kernel.core_pattern=|/bin/false' | sudo tee /etc/sysctl.d/90-nw-nocore.conf >/dev/null
sudo sysctl --system >/dev/null

say "完了。次は ./swap_off.sh → ./vault_init.sh の順に進めてください。"
