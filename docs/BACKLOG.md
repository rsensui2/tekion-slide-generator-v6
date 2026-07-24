# 改善バックログ

対応済みは削除していく。優先度順。（2026-07-25 Codexパイプラインレビューで全面更新）

## 高（正しさに関わる）

- **resume ハッシュの不足**: prompt+アンカーbasenameのみ。参照画像・ロゴ・footer・provider・
  image-size の変更を検知しない。生成条件を canonical 化して hash に含める
- **混在型 PPTX の取り込み欠落**: 画像スライドとネイティブスライドが混在すると、
  ネイティブ分だけ黙って落ちる。1ページでも抽出不能なら登録前にデッキ全体をPDF経由へ
- **OpenAI provider の契約不整合**: raw_dir / skip_finish / logo_path 未処理 + edit_slide の
  api_key が常に空で認証不能。Codex と同じ「raw保存→ロゴ合成→フッター」を実装し、
  キーの受け渡しを provider 別に整理する
- **feedback_worker の Codex 認証 warmup**: 並列編集の前に warmup_auth() を一度行い、
  子プロセスへ引き継ぐ（トークン更新競合の回避）

## 中（堅牢性・効率）

- **SID解決の全行スキャン**: /status 毎に sessions.db 全行のSHA1計算。sid列の追加 or
  ハブ内キャッシュで解消
- **legacy Handler の二重実装**: import/thumbnail が DashboardService と legacy 側に重複。
  エンドポイントは残し内部を Service へ委譲
- **タイムアウトの二重構造**: 外側 per-slide 600s と内側 420s×2 の不整合。一層に集約
- **AIMD が初回バーストに効かない**: probe batch か開始間隔付き token bucket
- **prompt生成の残骸/コスト**: extract_file_prefix 未使用、ProcessPool過剰、
  grounding_map はGemini時のみ生成に

## 低（体験・ドキュメント）

- **references/architecture.md が旧世代**: Claude SubAgent/Gemini/Phase 0〜5 の記述。
  現行（Phase 1〜9 + Hub/worker/queue）へ全面書き換え、旧スクリプトは legacy 明記
- **表紙先行パイプライン**: 表紙1枚を先行生成して計画時間を隠す
- **UI の push 更新**: SSE / 生成中のみ500msポーリング + カード単位DOM更新
- **legacy sticky port の記録競合**: 同時初回起動で最後の書き込みが勝つ（ハブ経路では無関係）
