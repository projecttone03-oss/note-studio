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

# notewriter（体験談・リサーチ型記事の制作支援、SPEC.md の機能1〜14）

仕様は `SPEC.md`。かけら管理・インタビュー・コンプライアンスチェッカー・下書き（体験談／リサーチ型）・壁打ち・文体学習・
公開準備（境界チェック・プレビュー・値付け・クロスセル・サムネイル）・SNS 導線・反応記録・執筆ペースがある。
どれも「提案・警告・下書き」までで、採用・公開・送信は人が行う（自動投稿・自動書き換えはしない）。

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
./nw draft generate 記事 区間    # 体験談の下書き（その区間のかけらだけ・ツールなし。送る前に yes で確認）
./nw draft show|versions|diff|restore|save   # 下書きの表示（段落番号・根拠・要確認の印）と版
./nw draft revise 記事 段落番号 "指示"      # 段落の書き直しを提案させる（採用するまで本文は変わらない）
./nw style show|edit|edits     # 文体ルール集と、AIの段落を人が直した記録
./nw style suggest|candidates|adopt|reject   # 手直しから文体ルールの候補（採用したものだけルール集に追記）
./nw book add|list|find|analyze              # 参考書籍の PDF（文字はアプリが取り出す）から文体ルールの候補
./nw kabeuchi start 記事 [--tmux]|status|stop # 壁打ち（確認実行でツール0を確かめてから claude --remote-control で起動）
./nw interview 記事             # 1問ずつ答えると、かけらとして保存
./nw rdraft 記事 [--materials M0001,…]       # リサーチ資料だけから比較表と下書き（資料IDつき・鮮度切れ警告）
./nw publish check|preview 記事 # 無料/有料の境界チェック・note スマホプレビュー
./nw price 記事   ./nw crosssell 記事   ./nw published add|list|rm   ./nw pace
./nw thumb 記事                 # サムネイル 1280×670（SVG・HTML、playwright か Chromium があれば PNG）
./nw sns list|draft|new|open|posted|health   # SNS（投稿はしない。Web Intent の URL を出すだけ）
./nw reaction import note.csv   # 反応記録を CSV から（取り込む前に確認）
./nw kakera suggest K0001       # 観点タグの候補
```

画面: ダッシュボードの「すぐやる」「次にやること」から始める。メニューは 集める・調べる・書く・届ける のまとまり。
スマホでは画面の下のタブ（ホーム・書く・答える・下書き・メニュー）を使う。

壁打ち（SPEC.md 機能4・絶対4）:
- 記事ごとに1つ、対話セッション方式 `claude --remote-control nw-記事名` で起動する（サーバーモード `claude remote-control` と `--bare` は使わない）。
- 起動の直前に、同じフラグ・同じ材料ファイルで `-p` の確認実行をし、init のツールが0個・MCPなしであることを確かめる。確かめられなければ起動しない。
- 材料（かけら・下書き・文体ルール）は作業フォルダの中のファイルを `--append-system-prompt-file` で渡す。
- 会話の履歴は `~/.claude/projects/` に残る。VPS ではここも暗号化フォルダの中に置く（`notewriter/ops/README.md`）。
- Remote Control の会話は Anthropic のサーバーに保存される（30日／モデル改善を許可していれば5年）。Trusted Devices を有効にする。

VPS の準備（暗号化・スワップ・バックアップ・復元テスト）: `notewriter/ops/README.md`（**未検証**。スクリプトは bash -n のみ）。

下書き（SPEC.md 第2段階の前半）:
- 本文は区間ごとに、その区間に割り当てたかけら（「保留」は除く）だけから作る。足りない所は `[要追加：〜]`。
- 段落ごとに根拠のかけらIDを表示し、根拠のかけらに見当たらない数字・セリフ・カタカナ語には「要確認」の印を付ける（警告だけで本文は変えない。機械的な目安なので、最終確認は人）。
- 作成・改稿・人の手直し・版の復元は、すべて版として残る。本文確定のたびに生成来歴を記録する。
- 下書きファイル（`drafts/`）をエディタで直接直した場合は、`./nw draft save` で版にするまで作成・改稿を止める（手直しが消えないように）。

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
