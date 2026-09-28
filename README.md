# note studio

noteで有料記事（情報整理型）を継続的に売るための、個人用ローカルツールです。
**AIは調べて提案するだけ、採用・承認は人が決める** 設計です。

## 今できること（MVP）

1. **需要リサーチ** — 「求められているのに良質な有料記事が少ないテーマ」を、出典・競合価格帯つきで候補化
2. **企画・差別化設計** — 採用したテーマについて、買う理由・差別化ポイント・無料/有料の境界・タイトル案・見出し構成案・価格を設計

各段階のあとに、画面で **採用／保留／却下**（テーマ）、**承認／修正依頼**（企画）を行うチェックポイントがあります。
人が採用していないテーマは、次の段階に進めない仕組みです。

## 使い方

1. Claude Code（このフォルダを開いた状態）で依頼する
   - 「需要リサーチして（ジャンル: おまかせ）」
2. 確認画面を開く
   ```
   ./ns serve
   ```
   ブラウザで http://127.0.0.1:8765 が開きます。出典と競合価格を確かめて、テーマを採用／保留／却下。
   判断の理由メモは、次回のリサーチに反映されます。
3. 採用したテーマの企画を依頼する
   - 「T003の企画を作って」
4. 画面でタイトル・見出し構成・価格を選んで承認（直したい点は「修正を依頼する」→「P005の修正依頼を反映して」）

## Macの電源が切れていても作業させる（クラウド）

1. スマホのClaudeアプリ（Codeタブ）か https://claude.ai/code で、このリポジトリを選んで依頼する
   （例:「需要リサーチして（ジャンル: ○○）」「T028の企画を作って」）。結果はクラウドに保存される。
2. Macを起動したら、この1コマンドで受け取り・取り込み・保存までを行う
   ```
   ./ns sync
   ```
3. `./ns serve` の画面で、いつもどおり採用・承認する（人の判断はMacでだけ行う）。

書籍PDFと `tools/pdftool` はアップロードしない（`.gitignore`）。`.ns-local` はこのMacが「判断する側」だという目印で、
別のMacで使うときは `touch .ns-local` を実行する。

## データ

| 場所 | 中身 |
|---|---|
| `data/studio.db` | すべてのデータ（SQLite） |
| `data/research/R001_日付.md` | リサーチごとのまとめ |
| `data/themes/T001_テーマ名/theme.md` | テーマの詳細（根拠・競合・判断ログ） |
| `data/themes/T001_テーマ名/plan_v1.md` | 企画（版ごと） |
| `data/raw/` | 取り込んだ元JSON（後から検証する用） |
| `inbox/` | Claude Code が書き出すJSONの置き場 |
| `references/books/` | 執筆の参考にしてほしい書籍（置き方は同フォルダの README.md） |

## 情報源のルール

- note内検索・note APIにはアクセスしません（note.com の robots.txt で禁止されているため）。note記事は一般のWeb検索で探します。
- X（旧Twitter）はスクレイピングしません。
- AIが拾った価格・数字は「未確認」扱い。自分で確かめたものだけ画面で確認済みにします。
- noteには記事投稿の公式APIがないため、自動投稿はしません（公開ボタンは人が押す前提）。

## 動作環境

macOS 標準の Python 3.9 以上。追加インストール不要（標準ライブラリのみ）。

テスト: `python3 -m unittest discover tests`

---

# notewriter（体験談・リサーチ型記事の制作支援、SPEC.md 第1段階）

仕様は `SPEC.md`。今あるのは **かけら管理** と **コンプライアンスチェッカー**（警告を出すだけで、本文は書き換えない）。

```
./nw init                      # 作業フォルダ（既定 workspace/）を作る。本番は gocryptfs のマウント先で
NW_DATA_DIR=~/nw-data ./nw serve --host 100.x.y.z   # 画面（既定 127.0.0.1:8766。外部公開は拒否、Tailscale のIPのみ可）
./nw kakera add|list|search|show|rm   ./nw neta add|list|promote   ./nw article add|list
./nw coverage 前編              # 区間×観点（五感・体の反応・セリフ・分岐点・感情）の充足度
./nw check 前編.md 後編.md      # 禁句・助言表現・属性・属性の集中・出典/年度・シリーズ内の矛盾
./nw rules                     # ルールファイルの場所と検証
./nw ideas run|list|set|to-research   # ネタ出し（Claude・ツールなし・追加料金なし。候補は「仮説」）
./nw research run --kind trend|deep --provider claude|pplx_standard|pplx_deep   # リサーチ（送る前に yes で確認）
./nw research list|show|import|usage|key set   # 資料・今月の費用・Perplexity の APIキー
./nw skills show|edit          # 得意・経験リスト（作業フォルダに保存）
./nw reaction add|list         # 反応記録（手入力）
```

流れ: ネタ出し（Claude、Web検索なし）→ 候補を選ぶ（人）→ トレンド調査（Claude／Perplexity）→ 深掘り調査。
- 外に送る文章は、送る前に全文を表示し、ぼかすべき語があれば警告する。かけらはネタ出し・リサーチに使わない。
- Perplexity は実行前に費用の目安を表示し、人が OK したときだけ実行。月の上限（`config/research.json` の `monthly_budget_jpy`）を超えると止まる。
  Claude が利用上限に達しても自動で Perplexity に切り替えない。
- Claude は別セッションで起動し、ツール制限（許可リスト・拒否リスト・Hook・起動時のツール一覧の確認）を重ねている。
  リサーチは WebSearch/WebFetch のみ、ネタ出しはツールなし。`--bare` は使わない。
- APIキーは作業フォルダの `secrets/`（権限 600）か環境変数 `PERPLEXITY_API_KEY`。画面・ログ・エラーには出さない。

- かけら・ネタ帳・下書き・来歴は作業フォルダにだけ保存し、`.gitignore` で除外している（リポジトリに入らない）。
- 作業フォルダに目印 `.nw-vault` がないと読み書きを拒否する（暗号化フォルダが未マウントのまま平文に書くのを防ぐ）。
- 書き込みは一時ファイル→fsync→rename の原子的な保存。
- ルール: `config/compliance_rules.json`（既定）、`config/kakera.json`（観点・状態）。実名や具体的な地名など
  個人の伏せ字リストは、作業フォルダ側の `config/compliance_rules.json` の `private_terms` に書く（リポジトリに入れない）。
- **実データは、暗号化・ログ無害化・一時ファイル/スワップ対策・暗号化バックアップ・復元テストが済むまで入力しない**（SPEC.md）。
