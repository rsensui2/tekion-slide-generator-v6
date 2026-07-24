# アーキテクチャ

```
Parent Claude (Sonnet)
  ↓ Pre-flight: .env.local → GEMINI_API_KEY確認
  ↓ Phase 0: SESSION_DIR構築
  ↓ Phase 1: design_guidelines.md作成（プリセット or カスタム）
  ↓
  ├─【ルートA】Phase 2: slides_plan.json手動作成（_grounding含む）+ バリデーション
  │
  └─【ルートB】Phase 2: SubAgent並列起動
     ├─ SubAgent 1 (Haiku): Read MD → H2/H3分析 → chunk_0.json
     ├─ SubAgent 2 (Haiku): Read MD → H2/H3分析 → chunk_1.json
     ...
     └─ SubAgent N (Haiku): Read MD → H2/H3分析 → chunk_N.json
     ↓ merge_chunks.py → slides_plan.json
     ↓ Phase 2.5: JSONバリデーション
  ↓
  ↓ Phase 3: Python 24並列（JSON + design_guidelines.md → Jinja2 → *.txt）
  │          + grounding_map.json 自動生成（_groundingフィールド抽出）
  ↓ Phase 3.5: render_test.py（任意）
  ↓ Phase 4: Gemini 3.1 Flash 並列スライド画像生成
  │  ├─ 初回並列生成（20並列、スライド毎グラウンディング制御）
  │  ├─ Thinking: High（高品質レイアウト推論）
  │  └─ 失敗検知 → 自動リトライ（Exponential Backoff、最大3回）
  ↓ Phase 4.5（任意）: 単一スライド再生成（バージョン管理: _v2, _v3...）
  ↓ Phase 5: PPTX/PDF自動作成（最新バージョンを自動選択 → *.pptx, *.pdf）
```

## API仕様（Gemini 3.1 Flash Image Preview）

| 項目 | 値 |
|------|-----|
| モデルID | `gemini-3.1-flash-image-preview` |
| エンドポイント | `https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent` |
| Thinking | `thinkingLevel: "minimal" \| "High"`（デフォルト: High） |
| 解像度 | `imageSize: "512px" \| "1K" \| "2K" \| "4K"`（デフォルト: 2K） |
| アスペクト比 | `aspectRatio: "16:9"` |
| グラウンディング | `tools: [{googleSearch: {searchTypes: {webSearch: {}, imageSearch: {}}}}]` |
| タイムアウト | 120秒（Thinking有効のため増加） |
| リトライ | 最大3回（Exponential Backoff: 2秒 → 4秒 → 8秒） |

### APIリクエスト構造

```json
{
  "contents": [{"parts": [{"text": "..."}]}],
  "generationConfig": {
    "responseModalities": ["IMAGE"],
    "imageConfig": {"aspectRatio": "16:9", "imageSize": "2K"},
    "thinkingConfig": {"thinkingLevel": "High", "includeThoughts": false}
  },
  "tools": [{"googleSearch": {"searchTypes": {"webSearch": {}, "imageSearch": {}}}}]
}
```

公式ドキュメント: https://ai.google.dev/gemini-api/docs/image-generation?hl=ja
