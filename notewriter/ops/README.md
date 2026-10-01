# VPS で notewriter を安全に動かす手順書

> **【未検証】** ここにあるスクリプトと手順は、クラウドの開発環境で `bash -n`（文法チェック）を通しただけです。
> 実際の VPS ではまだ一度も動かしていません。1つずつ実行し、出力を確かめながら進めてください。
> SPEC.md 絶対5のとおり、**下の「チェックリスト」がすべて終わるまで、実データ（ダミーでない事件の詳細）は入れないでください。**

## 全体の形

```
スマホ ──Tailscale──▶ VPS（ConoHa・Ubuntu 24.04）
                        ├─ ~/nw-cipher   … gocryptfs の暗号化された中身（ディスクに残るのはこれだけ）
                        ├─ ~/nw-data     … 復号ビュー（マウント中だけ見える）= notewriter の作業フォルダ
                        │    ├─ kakera/ drafts/ … かけら・下書きなど
                        │    ├─ claude-projects/ … Claude Code の会話履歴（~/.claude/projects からリンク）
                        │    └─ tmp/        … 一時ファイル（TMPDIR）
                        ├─ notewriter の画面（Tailscale のアドレスだけで待ち受け）
                        └─ 壁打ちセッション（tmux の中の claude --remote-control）
バックアップ: ~/nw-cipher を restic でさらに暗号化して外部へ
```

## 手順

| 順 | やること | スクリプト |
|---|---|---|
| 1 | パッケージ・Tailscale・ファイアウォール・コアダンプ無効 | `./setup_vps.sh` |
| 2 | スワップを止める（必要なら zram） | `./swap_off.sh [--zram]` |
| 3 | 暗号化フォルダを作る（マスターキーは紙に）・`~/.claude/projects` を中へ | `./vault_init.sh` |
| 4 | Claude Code を入れてログイン（サブスク認証。API キーは使わない） | 手で: `claude` を起動してログイン |
| 5 | 画面を起動（マウントされていなければ起動しない） | `./nw_start.sh` または `nw-web.service` |
| 6 | 外部への暗号化バックアップ | `./backup.sh --init`（初回）→ 以後 `./backup.sh` |
| 7 | 復元テスト（月1回） | `./restore_test.sh` |
| 8 | ログを短く残す | `journald-nw.conf` を `/etc/systemd/journald.conf.d/90-nw.conf` に置く |

再起動のあとは毎回 `./vault_mount.sh`（パスワード入力）→ `systemctl --user start nw-web.service`。
パスワードは保存しないので、自動では起動しません（これが安全側の設計です）。

設定は `~/.config/notewriter/ops.env` に書けます（例）:

```
NW_REPO=$HOME/note-studio
NW_CIPHER=$HOME/nw-cipher
NW_PLAIN=$HOME/nw-data
RESTIC_REPOSITORY=sftp:backup@backup-host:/srv/restic/nw
RESTIC_PASSWORD_FILE=$HOME/.config/notewriter/restic-pass
```

## 守っていること

- **平文への誤書き込みを防ぐ**: notewriter は作業フォルダの目印 `.nw-vault`（暗号化フォルダの中に作る）が見えないと読み書きしない。
  `vault_mount.sh` は、マウント前の同名フォルダに何か書かれていたら止める。`nw_start.sh` はマウントを確かめてから起動する。
- **Claude Code の会話履歴**: 壁打ち（対話セッション）の履歴は `~/.claude/projects/` に残る。ここを暗号化フォルダへのリンクにするので、
  マウントしていないときは書き込めない。
- **一時ファイル**: `TMPDIR` を暗号化フォルダの中にする。notewriter 自身の一時ファイルも保存先と同じフォルダに作る。
- **スワップ**: ディスク上のスワップを止め、コアダンプも無効にする（メモリの中身がディスクに残らないように）。
- **ログ**: notewriter の画面のログは「メソッドとパスの先頭」だけで、本文・記事名・検索語を出さない。journald は短く・メモリだけに。
- **バックアップ**: 暗号化された中身を、さらに restic で暗号化して外へ。鍵（gocryptfs のパスワード／マスターキー、restic のパスワード）は別々に紙でも保管。
- **外への公開なし**: 画面は Tailscale のアドレスだけで待ち受ける（notewriter は 0.0.0.0 を拒否する）。ufw で受け付けを閉じる。

## 実データを入れる前のチェックリスト

- [ ] `mount | grep nw-data` で gocryptfs がマウントされている。外すと `~/nw-data` が空に見える
- [ ] `ls -l ~/.claude/projects` が `~/nw-data/claude-projects` へのリンク
- [ ] `swapon --show` にディスク上のスワップがない／`ulimit -c` が 0
- [ ] 画面に Tailscale 以外（グローバル IP）からつながらない
- [ ] `./backup.sh` が成功し、`./restore_test.sh` で復元・マウントできた
- [ ] マスターキーと各パスワードを紙で保管した
- [ ] Anthropic アカウントの Trusted Devices を有効にした（Remote Control を使う場合）
- [ ] ダミーデータで、かけら → 下書き → 壁打ち → 公開準備 まで一通り動いた
