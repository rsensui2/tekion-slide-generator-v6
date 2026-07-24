# TEKION Slide Generator v6 — 「全枚数、確実に、同じ顔で。」

**Markdown / テキスト → 高品質な日本語 16:9 スライド → PPTX / PDF** を Claude Code から自動生成するスキル。
画像生成は **Codex 内蔵 gpt-image-2（ChatGPT/Codex サブスク枠）** を既定に、必要なら OpenAI API（従量課金）・
Gemini API にも切替可能。テスト用の mock プロバイダも内蔵。

## 4つの柱

| 柱 | 実装 |
|---|---|
| フルスロットル並列 | `--max-parallel auto` = 枚数ぶん一斉ファンアウト（上限20）。429検知で AIMD 自動減速 |
| ゼロ欠損保証 | manifest（台帳）+ 生成後の機械検証 + 検証スイープ + resume。「N枚頼んだらN枚返る」 |
| 直せる | 差分編集（現行画像参照）・赤ペン編集・バージョニング・ロールバック |
| 同じ顔 | スタイルアンカー画像 + 構造化デザインシステム（Brand Design System）の全プロンプト同一注入 |

## 構成

```
tekion-slide-generator-v6/
├── SKILL.md                 # 実行手順（Phase 1〜9）
├── scripts/
│   ├── generate_slides_parallel.py  # Phase 7: 並列生成オーケストレータ（manifest/スイープ/AIMD/resume）
│   ├── review_deck.py               # Phase 8: ブラウザレビューア（スライド毎フィードバック→JSON書き出し）
│   ├── edit_slide.py                # Phase 8: 差分編集・赤ペン編集・ロールバック
│   ├── manifest_utils.py            # 台帳・画像検証・エラー分類
│   ├── generate_prompts_from_json.py / validate_slides_json.py
│   ├── export_to_pptx.py / export_to_pdf.py   # Phase 9: manifest 駆動 export
│   ├── codex_app_server_client.py   # Codex ブリッジ（隔離 CODEX_HOME・サブスク枠）
│   └── providers/                   # codex / openai / mock
├── templates/               # balanced / visual の Jinja2 プロンプトテンプレート
├── references/              # デザインガイドライン・プリセット・トラブルシューティング
└── assets/logo.png
```

## クイックスタート

SKILL.md の Pre-flight → Phase 1〜9 に従う。要点:

```bash
# 生成（枚数ぶん並列・検証スイープ・resume 込み）
python3 scripts/generate_slides_parallel.py \
  --provider codex --prompts-dir <session>/prompts --output-dir <session>/images \
  --image-size 2K --logo assets/logo.png

# 失敗・中断後は同じコマンドを再実行するだけ（欠損分のみ生成）

# 差分編集とロールバック
python3 scripts/edit_slide.py --session-dir <session> --slide 02_solution_02 \
  --instruction "数値を52%に修正" --logo assets/logo.png
python3 scripts/edit_slide.py --session-dir <session> --slide 02_solution_02 --rollback

# export（検証済み確定版のみ）
python3 scripts/export_to_pptx.py --input-dir <session>/images \
  --manifest <session>/manifest.json --output deck.pptx
```

## ライセンス

公開（ソース公開）だが OSS ではない。未改変利用は可（商用含む）、改変・再配布は事前許可制。
