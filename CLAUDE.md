# note studio — Claude Code 向けルール

note で有料記事（情報整理型）を売るためのローカルツール。Claude Code は「調べて提案する役」、
人間は「判断する役」。**人間のチェックポイントを代行・スキップしないこと** が最重要ルール。

## 段階とスキル

| 段階 | スキル | 依頼例 | 状態 |
|---|---|---|---|
| 1. 需要リサーチ | `note-research` | 「需要リサーチして（ジャンル: ○○）」 | 実装済み |
| 2. 企画・差別化設計 | `note-plan` | 「T003の企画を作って」「P005の修正依頼を反映して」 | 実装済み |
| 3. リサーチ＆執筆 | — | | 未実装 |
| 4. レビュー | — | | 未実装 |
| 5. 公開準備 | — | | 未実装 |
| 6. 分析・改善 | — | | 未実装 |

notestudioの段階3〜6（執筆・レビュー・公開準備・分析）は実装しない。
この工程はnotewriter（nw）が担当する。

## 禁止事項

- `./ns decide`（テーマの採用・却下）と `./ns approve-plan`（企画の承認）を、ユーザーがチャットで
  明示的に頼んだ場合以外に実行しない。取り込み後は必ず止まって人間に確認を依頼する。
- note.com の `/search`・`/api/*` にアクセスしない（robots.txt で禁止）。個別記事の閲覧は1作業5ページまで。
- X（旧Twitter）をスクレイピングしない。
- 出典URL・数値・価格を推測で作らない。確認できなかったことはそう書く。
- note への自動投稿はしない（公式APIがないため。最終的な公開は人間が行う）。

## クラウド（Claude Code on the web）で作業するとき

`.ns-local` がない環境＝クラウド。DBは読み取り専用で、取り込み・判断・画面は使えない（コマンドが拒否する）。
- 使えるもの: `./ns show` `./ns context` `./ns themes` `./ns validate`
- 結果は `inbox/research_YYYYMMDD_xxx.json` / `inbox/plan_T<ID>_v<版>.json` に保存し、validate が OK になったら
  **inbox/ のファイルだけを** commit・push する（プログラムや CLAUDE.md は変更しない。変更しても Mac 側には取り込まれない）。
  ただし notewriter（nw）の開発は例外（下の「notewriter（nw）の開発」を参照）。
- 企画を作れるのは `./ns show` で状態が「採用・企画待ち」等のテーマだけ（Mac で人が採用したもの）。
- 書籍PDFはクラウドにない（著作物のためアップロードしない）。`tools/pdftool` も Mac 専用。書籍は参照できなかったと報告に書く。
- 公的資料のPDFを読むときは `pip install pypdf` などで文字を取り出して原文を確認する。
- 最後に「Macで `./ns sync` を実行すると取り込まれます」とユーザーに伝える。

## notewriter（nw）の開発（クラウドでの例外）

`SPEC.md` の note執筆支援アプリ。上の「inbox/ だけ commit」のルールは、nw の作業には適用しない。
- nw の作業では、`notewriter/`・`nw`・`config/`・`tests/`・`SPEC.md`・README の変更と commit を認める。
- 一方で、`notestudio/`・`./ns`・`data/` には触らない。
- nw の変更は `./ns sync` ではなく、プルリクエストのマージで取り込む（sync は inbox/*.json しか受け取らない）。
- かけら・下書きなどの実データは、作業フォルダ（既定 `workspace/`、本番は gocryptfs の復号ビュー）以外に置かない。commit もしない。

### notewriter 開発の引き継ぎメモ（2026-10-02 更新・ブランチ nw-tsubuyaki）

**できているもの**: SPEC.md の機能1〜14をひととおり（第1〜第4段階）。
- 2026-10-02 の変更依頼で、体験談の入口と下書きを作り直した: つぶやき・ボックス・かけらの移動（`boxes.py`）、
  ボックスのかけらから記事を1本書く下書き（`story.py`・`web_story.py`・`config/writing_prompts/story.md`）。
  ボックス = 今までの「記事」（articles.json）。1文ずつの根拠は版のブロックの `sentences`（`bridge` = Claude が足した文）。
  手直しは `writing._align_sentences` で文の単位に突き合わせ、文体学習の組を記録する。以前の区間ごとの下書き（/drafts）は残してある。
- 画面は機能ごとに `notewriter/web_*.py` に分け、`web.py` の `EXTENSIONS` から振り分けるだけ（各モジュールは
  `PREFIXES`・`route_get`・`post`・`back`）。公開準備の画面に足すカードは `web_publish.CARD_MODULES`。
- ツールなしの Claude を裏で1回呼ぶ処理は `notewriter/jobs.py`（確認トークン・同時実行の排他・init 検証）を使う。
  文体候補 `style_learn.py`・参考書籍 `books.py`・リサーチ型の下書き `research_writing.py`・SNS 投稿文 `sns.py`。
- 壁打ちは `kabeuchi.py`（`claude_runner.safety_flags` を確認実行と起動で共通に使う）。
- VPS 用の手順書とスクリプトは `notewriter/ops/`（**未検証**。bash -n のみ）。

**まだ確かめていないこと（Mac・VPS で人が確認する）**:
- 本物の Claude での確認: ボックスからの下書き（足した文の量・質問の答えやすさ）、壁打ちの起動（`claude --remote-control` のフラグ名・`--append-system-prompt-file`）、
  文体候補・書籍分析・リサーチ型の下書き・SNS 投稿文の出力の質。クラウドの claude はログインしていない。
- X・Threads の Web Intent の実機確認（済んだら `config/sns.json` の `intents_verified` を true に）。
- note の CSV の列名（`config/reactions_csv.json`）。Mac での `tools/pdftool` による書籍の取り込み。
- `notewriter/ops/` のスクリプトの実行。

**クラウドでの注意**:
- Mac の Python は **3.9.6**。クラウドの Python は新しいので、3.10 以降の書き方（`match`、実行時の `X | Y` 型、
  かっこ付きの複数 with、f文字列の式の中で外側と同じ引用符を使う・バックスラッシュを使う）を使わない。
  可能なら `uv python install 3.9` 等で 3.9 でもテストする。
- クラウドの claude CLI はログインしていないので、Claude を呼ぶ部分は偽の claude（`tests/test_writing.py` の `FAKE`）で
  テストする。本物での確認は、マージ後に Mac で行う（ダミーのかけらのみ。実データは SPEC.md 絶対5が済むまで使わない）。
- 作業はブランチで行い、`python3 -m unittest discover tests` がすべて通ってから push する。マージは人が PR で行う。

## 参考書籍（references/books/）

ユーザーが記事執筆の参考にしてほしい書籍を置く場所。`_書籍リスト.md` に「使う段階」と用途が書かれている。
- 各段階の作業前に `_書籍リスト.md` を読み、その段階に該当する書籍だけを参照する。
- 書籍はPDF（OCR済み）。全ページを読まず、`tools/pdftool` の `find` で関連ページを探し、`text` でそのページだけ読む:
  ```
  swiftc -O tools/pdftool.swift -o tools/pdftool   # tools/pdftool がなければ初回のみ
  tools/pdftool find "references/books/<ファイル>" 有料エリア 無料エリア
  tools/pdftool text "references/books/<ファイル>" 120 124
  tools/pdftool render "references/books/<ファイル>" 122 /tmp/p122.png   # 文字化け・印刷ページ番号の確認
  ```
- 公的資料のPDFを WebFetch で読めないときは、結果に出る保存先パス（`saved to ...`）を
  `tools/pdftool text <パス> 1 5` / `tools/pdftool find <パス> キーワード` で読み、原文で確認する。
- 書籍にある「note内検索を使った調査」は人間がやる作業。Claude Code は note の /search を使わない。
  必要ならユーザーに手で検索してもらい、見つけた競合を画面から追加してもらう。
- 書籍は「視点・構成の参考」「裏取りのきっかけ」として使う。事実は公的情報・一次情報で確認し直す。
- 書籍の要約・言い換えを記事の中身にしない。引用は必要最小限、主従・明瞭区別・出典明記（著作権法第32条）。
- 出典にするときは `{"type": "book", "title": 書名, "publisher": "著者／出版社", "location": "p.42 など"}`（URL不要）。
- 書籍ファイルの内容を外部サービスに送ったり、フォルダ外にコピーしたりしない。

## コマンド

```
./ns context                      # 既出テーマと判断ログ（リサーチ前に読む）
./ns show <テーマID>               # テーマと企画の詳細
./ns validate research|plan FILE  # JSON検証
./ns import-research FILE         # 段階1の結果を取り込み
./ns import-plan FILE             # 段階2の結果を取り込み（採用済みテーマのみ通る）
./ns serve                        # 人間用の確認画面 http://127.0.0.1:8765
python3 -m unittest discover tests
```

## 実装メモ

- Python 3.9 標準ライブラリのみ（追加インストール不要）。f文字列の式部分にバックスラッシュや同じ引用符を入れない（3.9では構文エラー）。
- DB（`data/studio.db`）が正、`data/` 以下のMarkdownは書き出し。スキーマ変更は `notestudio/db.py` の `MIGRATIONS` に追記する。
- 状態遷移（チェックポイント）は `notestudio/models.py` で強制している。
