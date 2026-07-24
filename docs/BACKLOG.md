# 改善バックログ（2026-07-24 Codex レビューより）

対応済みは削除していく。優先度順。

## 高（正しさに関わる）

- **manifest の lost update**: generator とダッシュボードが並行に load→save すると相互上書き。
  `fcntl.flock` で read-modify-write を排他する（atomic rename は破損防止のみ）
- **OpenAI provider の編集契約不整合**: raw_dir / skip_finish / logo_path を処理せず
  常にフッターのみ焼き込む。Codex provider と同じ「raw 保存 → ロゴ合成 → フッター」の
  順序を実装する（edit_slide/rebuild を --provider openai で使うと raw が作られない）
- **resume ハッシュの不足**: prompt+アンカー名のみ。参照画像・logo・footer・provider・
  image-size の変更を検知しない。生成設定を canonical JSON 化して hash に含める

## 中（堅牢性・整合）

- **タイムアウトの二重構造**: 外側 per-slide 600s と内側 420s×2 が不整合。
  一層に集約するか外側を内側から算出。タイムアウト時は process group ごと終了
- **古い prompts/*.txt の残留**: 構成縮小・改名時に旧スライドまで生成される。
  一時ディレクトリ生成→ディレクトリ置換 + manifest に obsolete 状態
- **AIMD が初回バーストに効かない**: 20枚以下は 429 観測前に全開始。probe batch か
  開始間隔付き token bucket
- **Codex サンドボックスの差**: read-only プロファイルでは localhost bind 不可 =
  ダッシュボード起動不能。起動失敗時のフォールバック（URL なし・ファイルベース進捗）を検討
- **Codex の長時間ブロック前提**: `--dashboard-timeout 7200` の前面ブロックがコマンド
  上限で殺される環境がある。短い待ち + 再接続可能なレビュー再開手順を明文化

## 低（速度・体験）

- **表紙先行パイプライン**: 表紙1枚を先に JSON→prompt→生成へ流し、生成中に残りの
  plan を書く（計画時間を隠す。体感効果最大）
- **セッション準備・warmup・ダッシュボードの並行化**（supervisor 化）
- **1枚ごとの codex exec 起動の排除**: デッキ単位の常駐 app-server 利用
- **UI の push 更新**: SSE / 生成中のみ 500ms ポーリング + カード単位の DOM 追加
  （ページ全体 reload とサムネイル同期生成を回避）
