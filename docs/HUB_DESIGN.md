# TEKION Slides Hub — 固定ポート常駐ダッシュボードの設計

## 背景 / 解決したい問題（すべて同根）

1. **ポートが毎回変わる**: セッション毎スティッキーポートは実装済みだが、新セッションの初回はランダム
2. **UIからのセッション起動がCodexで不安定**: `/open-session` の子サーバは「親サーバのプロセス」から
   生まれる。Codexのサンドボックスはコマンド終了時にプロセスツリーを皆殺しにする（切り離し起動でも
   生存不可、実測済み）ため、Codex管理下のサーバから生まれた子は必ず死ぬ
3. **ブラウザ問題**: エージェントがサーバを立てるたびにブラウザを開く/開かない問題が発生。
   ユーザーは「どのポートで上がるか分からない」

**根本原因: サーバの寿命がエージェントのコマンドの寿命に縛られていること。**

## 解決アーキテクチャ

**エージェントから独立した常駐ハブサーバを1本、固定ポートで立てる。**

```
[launchd (KeepAlive)] ──> hub_server.py :7799 （固定。env TEKION_DASHBOARD_PORT で変更可）
                             ├── GET /            … ハブ（RECENT SESSIONS カードグリッド等、現 /home 相当）
                             ├── GET /s/<sid>/    … 各セッションのデッキビュー
                             ├── GET /healthz     … 死活確認（JSON: {ok, version, pid}）
                             └── 既存全エンドポイントを /s/<sid>/ 配下にセッションスコープ化

[エージェント(Claude/Codex)] … サーバを一切立てない。
   生成・編集はファイル操作のみ（従来どおり）。
   レビュー待ちは review_deck.py --await-feedback（ファイル監視。サンドボックス内で安全）
```

- ユーザーは `http://127.0.0.1:7799/` を**1つブックマークするだけ**。ポートは二度と変わらない
- カードクリック = ただのページ遷移（`/s/<sid>/`）。子サーバ起動が消滅し、Codexでも完全に安定
- エージェントがブラウザを開く必要がなくなる（開くとしても常に同じURL）

## コンポーネント設計

### 1. `scripts/hub_server.py`（新規）

- `ThreadingHTTPServer`、bind は **127.0.0.1 のみ**。ポート: `TEKION_DASHBOARD_PORT`（default 7799）
- **sid** = セッション実パスの `sha1(realpath)[:12]`。sessions.db（台帳）に登録済みのパスのみ解決可
  （任意パス読み出し防止。未登録は 404）。逆引きは台帳を SELECT
- ルーティング:
  - `/` … ハブページ。現在の landing（ヒーロー・バナー・RECENT SESSIONS カード・使い方3ステップ）を
    セッション非依存に再構成。カードの遷移先は `/s/<sid>/`
  - `/s/<sid>/` … デッキビュー（現 build_html の deck ページ）
  - `/s/<sid>/<endpoint>` … status / thumb/... / session-thumb / prompt / select-version /
    reorder / delete-slide / restore-slide / import / export/pptx / export/pdf / feedback
  - `/healthz` … `{"ok": true, "version": "<plugin version>", "port": N}`
- 実装方針: **review_deck.py の Handler・build_html を最大限再利用**する。
  - build_html に `base_path: str = ""` を追加し、HTML内のfetch/リンクを `${BASE}` 前置に統一
    （JS冒頭に `const BASE = "__BASE_PATH__";` を注入。fetch('/status') → fetch(BASE + '/status')）
  - 画像srcは現在も相対パス（`images/...` / `thumb/...`）なので `/s/<sid>/` 配下でそのまま動く
  - Handler のセッション文脈（session_dir / manifest_path / manifest_lock）をリクエスト毎に
    sid から解決する形にリファクタ（manifest_lock はセッション毎に dict で保持）
- `/import` の new_session、`/open-session` は**ページ遷移に置換**:
  - new_session import: セッション作成→取り込み→台帳登録→ `{"url": "/s/<sid>/"}` を返す（子サーバ起動なし）
  - RECENT SESSIONS カード: `<a href="/s/<sid>/">`（JS不要）
- フィードバック: `/s/<sid>/feedback` は保存のみ（サーバは絶対に終了しない）。
  **未処理バッジ**: デッキビューで `slide_feedback.json` の mtime が「最新の images/*.png の mtime」より
  新しい場合、ヘッダーに「⏳ 未処理の修正指示あり（HH:MM送信）— エージェントに『続きを』と伝えてください」
  を表示する（チャットを閉じた後に送られた指示の可視化）

### 2. デーモン登録 `scripts/install_hub.sh`（新規・macOS）

- `~/Library/LaunchAgents/jp.tekion.slides.hub.plist` を書いて `launchctl bootstrap gui/$UID`
  （`RunAtLoad=true`, `KeepAlive=true`、ログは `~/.tekion-slides/hub.log`）
- python3 の絶対パスを `command -v python3` で焼き込む（launchd は PATH が薄い）
- 再インストール時は bootout → bootstrap（冪等）
- `--uninstall` オプション
- Linux/WSL 向けに systemd user unit の雛形をコメントで併記（実装は macOS 優先）
- ハブ自身の自動更新: plist はスキルディレクトリ直指しにせず、
  `~/.tekion-slides/hub/hub_server.py` へ**コピーしたもの**を起動する（プラグイン更新でスキルの
  パスが変わっても壊れないように）。install_hub.sh 実行時に最新をコピー。
  hub の `/healthz` が返す version と SKILL 側の期待が食い違ったら SKILL がコピー+再起動を促す

### 3. SKILL.md の変更（エージェント契約）

- Phase 1: サーバ起動を廃止し、次に置換:
  1. `curl -fsS http://127.0.0.1:${TEKION_DASHBOARD_PORT:-7799}/healthz` でハブ死活確認
  2. 生きていれば: URL `http://127.0.0.1:7799/s/<sid>/` をユーザーに提示（sid はセッション作成後に
     `session_registry.py --register` が出力する。--register は sid を stdout に出すよう拡張）
  3. 死んでいれば:
     - **Claude Code**（サンドボックス外でプロセスを起こせる）: `install_hub.sh` を実行して起動
     - **Codex**（起こせない）: ユーザーに1行案内
       「ターミナルで `bash <skill>/scripts/install_hub.sh` を1回実行してください（初回のみ）」
       それまでのフォールバックとして従来の `--serve`（前面）も可
- Phase 7: `--with-dashboard` はハブがあるときは不要（実況はハブが manifest を読んで出す）。
  ハブ無し環境のフォールバックとして温存
- Phase 8: 両エージェントとも `review_deck.py --await-feedback`（Codex=前面 / Claude=バックグラウンド）。
  従来の `--serve` / `--persist` はハブ無し環境のフォールバックとして全部残す（**後方互換は壊さない**）

### 4. 既存コードの温存（重要）

- `review_deck.py --serve / --persist / --await-feedback / 静的モード` は**そのまま動き続けること**
  （ハブ未導入の受講生・Linux・緊急時のフォールバック）
- generate_slides_parallel の `--with-dashboard` も温存
- 台帳（sessions.db）・manifest・編集系スクリプトは変更不要のはず（変更するなら理由を明記）

## テスト要件（実装に含めること）

- ハブをエフェメラルポートで起動する統合テスト:
  - 2セッション（mock画像）を台帳登録 → `/` に両方のカード、`/s/<sid>/` で各デッキ表示
  - `/s/<sid>/select-version`・`/reorder`・`/delete-slide`・`/feedback`（保存されサーバ継続）
  - `/s/<sid>/export/pptx` が200
  - 未登録sid → 404、パストラバーサル不可
  - feedback 後の未処理バッジがHTMLに出る
- `python3 scripts/check_dashboard_js.py` が通ること（**注意**: PAGE_TEMPLATE は Python 文字列。
  JS内で `'\n'` と書くと生改行が入りスクリプト全体が死ぬ事故が実際に起きた。JSに手を入れたら必ず実行）
- 既存の `--serve` 経路のスモーク（起動→ / 応答→ feedback で exit 0）

## 実装手順（Codex への指示）

1. このリポジトリで `git checkout -b feature/hub-daemon`
2. 上記設計をレビューし、問題点・改善案があれば `docs/HUB_DESIGN.md` に「## Codexレビュー所見」
   として追記した上で、良いと思う形に**修正して**実装する（設計への盲従は不要。ただし
   「後方互換を壊さない」「bind 127.0.0.1」「登録済みセッションのみ配信」は不変条件）
3. 実装 → テストを書いて実行 → check_dashboard_js.py → 全部緑にする
4. `git add -A && git commit`（ブランチにコミット。push はしない）
5. 変更概要・テスト結果・残課題を最終メッセージで報告する
