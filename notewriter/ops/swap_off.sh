#!/usr/bin/env bash
# 【未検証】スワップ対策: メモリの中身（下書き・かけら）がディスクのスワップに書き出されないようにする。
# 方針: ディスク上のスワップは止める。メモリが足りなければ zram（メモリ内の圧縮スワップ。ディスクに書かない）を使う。
# 使い方: ./swap_off.sh [--zram]
set -euo pipefail
. "$(dirname "$0")/common.sh"

confirm "ディスク上のスワップを止め、/etc/fstab のスワップ行をコメントにします。"

sudo swapoff -a
sudo cp /etc/fstab "/etc/fstab.bak.$(date +%Y%m%d%H%M%S)"
sudo sed -i -E 's@^([^#].*\sswap\s.*)$@# nw-disabled \1@' /etc/fstab
# ConoHa のイメージで /swap.img がある場合
if [ -f /swap.img ]; then
  sudo rm -f /swap.img
fi
echo 'vm.swappiness=1' | sudo tee /etc/sysctl.d/90-nw-swap.conf >/dev/null
sudo sysctl --system >/dev/null

if [ "${1:-}" = "--zram" ]; then
  sudo apt-get install -y zram-tools
  printf 'ALGO=zstd\nPERCENT=50\n' | sudo tee /etc/default/zramswap >/dev/null
  sudo systemctl restart zramswap
  say "zram を有効にしました（ディスクには書きません）。"
fi

say "確認: 'swapon --show' の出力にディスク上のスワップ（/swap.img など）がないこと。"
swapon --show || true
