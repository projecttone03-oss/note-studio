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
- 企画を作れるのは `./ns show` で状態が「採用・企画待ち」等のテーマだけ（Mac で人が採用したもの）。
- 書籍PDFはクラウドにない（著作物のためアップロードしない）。`tools/pdftool` も Mac 専用。書籍は参照できなかったと報告に書く。
- 公的資料のPDFを読むときは `pip install pypdf` などで文字を取り出して原文を確認する。
- 最後に「Macで `./ns sync` を実行すると取り込まれます」とユーザーに伝える。

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
