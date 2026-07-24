# nano-banana Slide Generator v3 Flash

> Gemini 3.1 Flash Image Preview（Nano Banana 2）を使った高品質プレゼンスライド自動生成スキル

Claude Code のスキルとして動作し、Markdown やテキストから 16:9 の美しいプレゼンスライドを並列生成して PPTX/PDF に出力します。

## v3 Flash の特徴

| 機能 | v2 | v3 Flash |
|------|:--:|:--------:|
| モデル | Gemini 3 Pro | **Gemini 3.1 Flash** |
| グラウンディング | - | **Google画像検索（スライド単位ON/OFF）** |
| Thinking | - | **High / minimal 選択可** |
| 解像度 | 2K固定 | **512px / 1K / 2K / 4K** |
| 承認プロンプト | 多数 | **全Phase自動実行** |

## クイックスタート

### 1. 前提条件

- Python 3.10+
- [Gemini API Key](https://aistudio.google.com/apikey)

### 2. APIキーの設定

```bash
# いずれかの場所に .env.local を作成
echo "GEMINI_API_KEY=your-api-key-here" > ~/.claude/.env.local
```

### 3. 初回セットアップ

```bash
SKILL_DIR=~/.claude/skills/nanobanana-slide-generator-v3-flash
bash ${SKILL_DIR}/scripts/setup.sh
```

### 4. スライド生成

Claude Code で以下のように依頼するだけで、スキルが自動トリガーされます:

```
「AIの未来」というテーマで10枚のプレゼン資料を作って
```

```
この Markdown をスライドにして → docs/proposal.md
```

```
VibeCoder Bootcamp 第4回のスライドを生成して
```

スキルを明示的に呼び出す場合:

```
/nanobanana-slide-generator-v3-flash
```

## 使い方ガイド

### 2つのルート

#### ルートA: 高品質モード（デフォルト）

ピッチ資料・提案書・セミナーデモなど、見た目を重視する 10-15 枚向け。

```
入力テキスト → デザインガイドライン作成 → slides_plan.json 手動作成
→ Jinja2 プロンプト生成 → Gemini 並列生成 → PPTX/PDF
```

**所要時間**: JSON作成 5-10分 + 画像生成 2-3分

#### ルートB: 高速モード

既存 Markdown の一括スライド化、20 枚以上の研修テキストなど、速度を重視する場合。

```
MD群 → デザインガイドライン → SubAgent 並列で JSON 自動生成
→ Jinja2 プロンプト生成 → Gemini 並列生成 → PPTX/PDF
```

**所要時間**: 全自動 2-3分

#### どちらを選ぶ？

| 言い方 | ルート |
|--------|--------|
| 「サクッと」「一括で」「まとめてスライドにして」 | B |
| 「いい感じに」「ちゃんと」「プレゼン用に」 | A |

## Phase 別フロー

### Pre-flight: APIキー確認

`.env.local` から `GEMINI_API_KEY` を読み込み、依存パッケージの存在を確認します。

### Phase 0: セッション準備

タイムスタンプ付きのセッションディレクトリを作成します。

```
{OUTPUT_DIR}/slides_output/2026-02-27_2306/
├── json/       # Phase 2
├── prompts/    # Phase 3
└── images/     # Phase 4
```

### Phase 1: デザインガイドライン作成

スライド全体のトーンを決定する `design_guidelines.md` を作成します。

- **プリセット**: サンプルプリセット。ブランド毎にカスタマイズ可
- **カスタム**: テンプレートを参照し、カラーパレット・写真スタイル・トーンを指定

テンプレートは `references/design_guidelines_template.md` にあります。

### Phase 2: slides_plan.json 作成（最重要）

入力テキストを分析し、各スライドの内容を JSON で定義します。
このフェーズがスライドの品質を最も左右します。

```json
{
  "slides": [
    {
      "slide_number": 0,
      "source_file": "00_cover",
      "title": "AI時代の富の移転",
      "subtitle": "2025年最新トレンド",
      "content": "<!-- Pattern G --> 講座タイトルスライド（表紙）...",
      "key_message": "AIがもたらす新しい経済パラダイム",
      "_grounding": false
    }
  ],
  "total_slides": 12
}
```

**フィールド定義**:

| フィールド | 必須 | 型 | 説明 |
|-----------|:----:|:--:|------|
| `slide_number` | Yes | 数値 | スライド番号（0始まり） |
| `source_file` | Yes | 文字列 | ファイル識別名（`00_cover`, `01_テーマ名`...） |
| `title` | Yes | 文字列 | スライドタイトル |
| `subtitle` | Yes | 文字列 | サブタイトル |
| `content` | Yes | 文字列 | スライド内容（レイアウトパターン + テキスト + 図解指示） |
| `key_message` | No | 文字列 | そのスライドの核心メッセージ 1文 |
| `_grounding` | No | 真偽値 | Google画像検索グラウンディング（デフォルト: false） |

**禁止フィールド**: `slide_type`, `layout`, `visual_description`（デザイン判断は Gemini が行う）

### Phase 3: プロンプト生成 + グラウンディングマップ

Jinja2 テンプレートで各スライドの画像生成プロンプトを 24 並列で生成します。
同時に `_grounding` フィールドを抽出して `grounding_map.json` を自動生成します。

### Phase 4: Gemini 3.1 Flash 並列画像生成

最大 20 並列で Gemini API を呼び出し、スライド画像を生成します。

**パラメータ**:

| パラメータ | デフォルト | 選択肢 | 説明 |
|-----------|:---------:|--------|------|
| `--image-size` | 2K | 512px, 1K, 2K, 4K | 出力解像度 |
| `--thinking-level` | High | minimal, High | レイアウト推論の品質 |
| `--grounding-map` | - | パス | スライド別グラウンディング設定 |
| `--logo` | - | パス | 全スライドに配置するロゴ |

**解像度一覧**:

| 名称 | ピクセル | 用途 |
|:----:|:-------:|------|
| 512px | 912x512 | プレビュー・下書き確認 |
| 1K | 1376x768 | Web用・軽量プレゼン |
| 2K | 2752x1536 | **標準品質（推奨）** |
| 4K | 4096x2304 | 大型ディスプレイ・印刷用 |

### Phase 4.5: 単一スライド再生成（任意）

気に入らないスライドだけを再生成できます。既存ファイルは保持され、バージョン管理されます。

```
オリジナル:    slide_01.png       （暗黙の v1）
再生成1回目:  slide_01_v2.png
再生成2回目:  slide_01_v3.png
```

Phase 5 では最新バージョンが自動選択されます。

### Phase 5: PPTX/PDF 出力

生成された画像を PowerPoint と PDF に変換します。

## グラウンディング制御

v3 Flash の目玉機能です。スライドごとに Google 画像検索グラウンディングの ON/OFF を制御できます。

### いつ有効にする？

| `_grounding: true` | `_grounding: false`（デフォルト） |
|---------------------|-----------------------------------|
| 実在する製品・ブランドのビジュアル参照 | 抽象的な概念図・フロー図 |
| 特定の都市・建築物の正確な描写 | テキスト中心のリスト・箇条書き |
| 市場データに基づくリアルなビジュアル | 表紙・中扉・まとめ等の定型スライド |
| 実在する組織のロゴ的要素 | オリジナルのイラスト・アイコン |

### 仕組み

```
Phase 2: slides_plan.json に _grounding フィールドを記述
    ↓
Phase 3: generate_prompts_from_json.py が grounding_map.json を自動生成
    ↓
Phase 4: generate_slides_parallel.py がスライドごとに grounding ON/OFF を切り替え
    ↓
        grounding ON  → tools: [{googleSearch: {searchTypes: {webSearch: {}, imageSearch: {}}}}]
        grounding OFF → tools なし（通常生成）
```

グラウンディングが有効なスライドのメタデータ（検索クエリ、参照元URL等）は `grounding/` ディレクトリに自動保存されます。

## レイアウトパターン

`content` フィールドに `<!-- Pattern X -->` コメントを含めることで、Gemini にレイアウトのヒントを与えます。

| パターン | 用途 | 構造 |
|:--------:|------|------|
| **A** | 比較・対比 | 左50%テキスト + 右50%ビジュアル |
| **B** | プロセス・フロー | 角丸ボックス + 矢印の流れ |
| **C** | 3項目以上のリスト | 等分割グリッド、アイコン付きカード |
| **D** | 重要な数字・結論 | 画面中央に巨大要素 |
| **E** | 階層構造・体系図 | ピラミッド/ツリー/同心円 |
| **F** | 抽象概念・メタファー | 60-70%イラスト + 最小テキスト |
| **G** | インパクト・表紙・CTA | 全画面写真 + オーバーレイ |
| **H** | 多数の小項目 | 4x3 アイコングリッド |
| **I** | タイムライン | 時間軸 + マイルストーン |
| **J** | データ・統計 | グラフ・チャート |
| **K** | システム全体像 | 全画面概念図 + ノード |
| **L** | 中扉（セクション区切り） | 中央タイトル + グラデーション背景 |
| **M** | 講座タイトル（表紙） | 3層（講座名・組織名・回次） |

## ディレクトリ構造

```
nanobanana-slide-generator-v3-flash/
├── SKILL.md                          # スキル定義（Claude Code が読み込む）
├── CLAUDE.md                         # Claude Code コンテキスト
├── README.md                         # このファイル
├── requirements.txt                  # Python 依存パッケージ
│
├── scripts/
│   ├── setup.sh                      # 初回セットアップ（依存パッケージ自動インストール）
│   ├── generate_slide_with_retry.py  # 単一スライド生成（Gemini API呼び出し）
│   ├── generate_slides_parallel.py   # 並列スライド生成オーケストレーター
│   ├── generate_prompts_from_json.py # JSON → Jinja2 プロンプト生成 + grounding_map
│   ├── validate_slides_json.py       # slides_plan.json バリデーター
│   ├── regenerate_slide.py           # 単一スライド再生成（バージョン管理）
│   ├── render_test.py                # プロンプト検証（API呼び出し前確認）
│   ├── merge_chunks.py               # ルートB: チャンク JSON 統合
│   ├── export_to_pptx.py             # 画像 → PowerPoint 変換
│   └── export_to_pdf.py              # 画像 → PDF 変換
│
├── templates/
│   └── prompt_template.j2            # Jinja2 テンプレート（ペルソナ + 制約 + パターン）
│
├── references/
│   ├── design_guidelines_template.md # デザインガイドラインの汎用テンプレート
│   └── presets/
│       └── example-preset.md          # サンプルプリセット（カスタマイズ可）
│
├── assets/
│   └── logo.png                      # サンプルロゴ（差し替え推奨）
│
└── tests/
    └── test_v3_flash_features.py     # v3 Flash 機能テスト（51テスト）
```

## セッション出力構造

各実行で以下のディレクトリが作成されます:

```
{OUTPUT_DIR}/slides_output/2026-02-27_2306/
├── json/
│   └── slides_plan.json        # スライド計画 JSON
├── prompts/
│   ├── 00_cover_01.txt         # 各スライドの Gemini プロンプト
│   ├── 01_market_01.txt
│   └── ...
├── images/
│   ├── 00_cover_01.png         # 生成されたスライド画像
│   ├── 01_market_01.png
│   ├── 01_market_01_v2.png     # 再生成バージョン
│   └── ...
├── grounding/
│   └── 01_market_02_grounding.json  # グラウンディングメタデータ
├── grounding_map.json          # スライド別グラウンディング設定
├── design_guidelines.md        # デザインガイドライン
├── presentation.pptx           # PowerPoint 出力
└── presentation.pdf            # PDF 出力
```

## スクリプト詳細リファレンス

### generate_slide_with_retry.py

Gemini API を呼び出して単一スライドを生成するコアスクリプト。

```bash
python generate_slide_with_retry.py \
  --prompt "プロンプトテキスト" \
  --output "output.png" \
  --api-key "YOUR_KEY" \
  --image-size 2K \
  --thinking-level High \
  --grounding \
  --max-retries 3 \
  --logo logo.png
```

**API リクエスト構造**:

```
POST /v1beta/models/gemini-3.1-flash-image-preview:generateContent

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

- タイムアウト: 120秒（Thinking有効のため v2 の 60秒から増加）
- リトライ: Exponential Backoff（2秒 → 4秒 → 8秒）
- 429/503 エラーは自動リトライ

### generate_slides_parallel.py

複数スライドを最大 20 並列で生成するオーケストレーター。

```bash
python generate_slides_parallel.py \
  --prompts-dir prompts/ \
  --output-dir images/ \
  --api-key "YOUR_KEY" \
  --max-parallel 20 \
  --grounding-map grounding_map.json \
  --image-size 2K \
  --thinking-level High
```

`grounding_map.json` からスライドごとのグラウンディング設定を読み取り、各サブプロセスに `--grounding` フラグを渡します。

### generate_prompts_from_json.py

slides_plan.json を読み込み、Jinja2 テンプレートで各スライドのプロンプトを生成。

```bash
python generate_prompts_from_json.py \
  --session-dir SESSION_DIR \
  --json-file json/slides_plan.json \
  --output-dir prompts \
  --design-guidelines design_guidelines.md \
  --image-size 2K
```

**v3 Flash 固有の機能**:
- `_grounding` フィールドを抽出して `grounding_map.json` を自動生成
- `--image-size` に応じた解像度をテンプレートに渡す

### validate_slides_json.py

slides_plan.json の構造を検証。

```bash
python validate_slides_json.py --file slides_plan.json
```

検証項目:
- 必須フィールドの存在（slide_number, source_file, title, subtitle, content）
- 禁止フィールドの不在（slide_type, layout, visual_description 等）
- `_` プレフィックス付きフィールドは内部用として許可（`_grounding`, `_file_slide_number` 等）

### regenerate_slide.py

特定の 1 枚だけを再生成（バージョン管理付き）。

```bash
python regenerate_slide.py \
  --slide "01_market_02" \
  --session-dir SESSION_DIR \
  --api-key "YOUR_KEY" \
  --grounding
```

## デザインガイドラインのカスタマイズ

### テンプレートの構成

`references/design_guidelines_template.md` は以下のセクションで構成されています:

1. **配色パレット** — ベース、アクセント、サブアクセント、エリア背景、文字色
2. **フォント** — 見出し・本文の書体指定
3. **写真スタイル** — 被写体、シーン、色調、構図
4. **トーン & ムード** — 1-2文のビジュアルトーン
5. **技術仕様** — アスペクト比、解像度、フッター、余白
6. **レイアウトパターン** — Pattern A-M の定義
7. **図解パターンガイド** — 図解タイプの選択基準

### プリセットの使い方

スキルが自動で実行する手順（`SKILL.md` Phase 1 と同じ）。`references/presets/.active_preset` にプリセット名が記録されていればそれを優先し、無ければ `example-preset.md` にフォールバックする。

```bash
ACTIVE_PRESET_FILE="${SKILL_DIR}/references/presets/.active_preset"
if [ -f "${ACTIVE_PRESET_FILE}" ]; then
  PRESET_NAME=$(cat "${ACTIVE_PRESET_FILE}")
  PRESET_PATH="${SKILL_DIR}/references/presets/${PRESET_NAME}"
  [ -f "${PRESET_PATH}" ] || PRESET_PATH="${SKILL_DIR}/references/presets/example-preset.md"
else
  PRESET_PATH="${SKILL_DIR}/references/presets/example-preset.md"
fi
cp "${PRESET_PATH}" "${SESSION_DIR}/design_guidelines.md"
```

`.active_preset` は [design-setup](https://github.com/rsensui2/tekion-slide-generator/tree/main/design-setup) スキルが対話的に書き出すか、手動で `echo "my-brand.md" > references/presets/.active_preset` で設定できる。

### 新しいプリセットの作成

1. `references/design_guidelines_template.md` をコピー
2. `[プレースホルダー]` を実際の値に置き換え
3. `references/presets/` に保存

```bash
cp ${SKILL_DIR}/references/design_guidelines_template.md \
   ${SKILL_DIR}/references/presets/my_project.md
# → 編集して配色・トーンをカスタマイズ
```

## Thinking モード

Gemini 3.1 Flash の Thinking 機能により、レイアウト推論の品質を制御できます。

| レベル | 説明 | 用途 |
|:------:|------|------|
| **High** | 深い推論。レイアウトの最適化に時間をかける | 本番品質のスライド（デフォルト） |
| **minimal** | 最小限の推論。高速だがレイアウトはシンプル | 下書き・プレビュー |

`includeThoughts` は常に `false` に設定されているため、Thinking の中間出力はレスポンスに含まれません。最終画像のみが返されます。

## トラブルシューティング

### APIキーエラー

```
❌ エラー: GEMINI_API_KEYが設定されていません
```

`.env.local` ファイルに `GEMINI_API_KEY=...` を設定してください:

```bash
echo "GEMINI_API_KEY=your-key" > ~/.claude/.env.local
```

### パッケージが足りない

```
ModuleNotFoundError: No module named 'PIL'
```

セットアップスクリプトを実行すると不足パッケージが自動インストールされます:

```bash
bash ~/.claude/skills/nanobanana-slide-generator-v3-flash/scripts/setup.sh
```

### タイムアウトエラー

Thinking: High + 4K 解像度の組み合わせでは処理時間が長くなります。
`--max-retries 3` で自動リトライされますが、頻発する場合は解像度を下げるか Thinking を minimal にしてください。

### 表紙が末尾に来る

`source_file` を `"00_cover"` に設定してください。ファイル名のソート順でスライド順序が決まります。

### グラウンディングメタデータ

グラウンディングが有効なスライドのメタデータは `grounding/` ディレクトリに自動保存されます:

```json
{
  "imageSearchQueries": ["AI market trend 2025"],
  "groundingChunks": [
    {"web": {"uri": "https://example.com", "title": "..."}}
  ]
}
```

### SubAgent が見つからない（ルートB）

```bash
cp ~/.claude/skills/nanobanana-slide-generator/agents/nanobanana-prompt-generator-subagent.md \
   ~/.claude/agents/
```

## テスト

v3 Flash の新機能は 51 のユニットテストでカバーされています:

```bash
cd ~/.claude/skills/nanobanana-slide-generator-v3-flash
python3 tests/test_v3_flash_features.py
```

テスト対象:
- 解像度マッピング（4種類の解像度名 → ピクセルサイズ変換）
- grounding_map 生成・保存・読み込み
- `_grounding` フィールドのバリデーション通過
- Jinja2 テンプレートの解像度動的レンダリング
- `build_payload` のグラウンディング/Thinking パラメータ構造
- API レスポンスからの画像・メタデータ抽出
- E2E Phase 2→3 パイプライン
- モデル ID・定数の正確性

## API 仕様

```
モデルID:         gemini-3.1-flash-image-preview
エンドポイント:   https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent
Thinking:         thinkingLevel: "minimal" | "High"
解像度:           imageSize: "512px" | "1K" | "2K" | "4K"
アスペクト比:     aspectRatio: "16:9"
グラウンディング: tools: [{googleSearch: {searchTypes: {webSearch: {}, imageSearch: {}}}}]
タイムアウト:     120秒
リトライ:         最大3回（Exponential Backoff）
```

公式ドキュメント: https://ai.google.dev/gemini-api/docs/image-generation?hl=ja

## 依存パッケージ

| パッケージ | バージョン | 用途 |
|-----------|:---------:|------|
| Pillow | >= 10.0.0 | 画像処理（PDF変換） |
| python-pptx | >= 0.6.21 | PowerPoint 生成 |
| requests | >= 2.31.0 | Gemini API 呼び出し |
| Jinja2 | >= 3.1.0 | プロンプトテンプレート |

## アセット

- [assets/logo.png](assets/logo.png) — サンプルロゴ（ご自身のブランドロゴに差し替え推奨）

Phase 4/4.5 で `--logo` を指定すると全スライド右下にロゴ挿入。ロゴ不要の場合は `--logo` を省略。

## デザインリソース

| ファイル | 用途 |
|----------|------|
| [references/design_guidelines_template.md](references/design_guidelines_template.md) | デザインガイドライン汎用テンプレート |
| [references/presets/](references/presets/) | プリセット集（サンプル込み） |
| [references/architecture.md](references/architecture.md) | アーキテクチャ図 + API仕様 |
| [references/troubleshooting.md](references/troubleshooting.md) | トラブルシューティング |
| [references/quality-checklist.md](references/quality-checklist.md) | 品質チェックリスト |
| [templates/prompt_template.j2](templates/prompt_template.j2) | Jinja2テンプレート |

## バージョン履歴

### v3 Flash（現在）
- Gemini 3.1 Flash Image Preview（Nano Banana 2）対応
- Google画像検索グラウンディング（スライド単位制御）
- Thinking モード（High/minimal）
- 解像度選択（512px/1K/2K/4K）
- 全Phase自動実行による承認回数削減

### v2
- Gemini 3 Pro Image Preview
- Jinja2 テンプレートベースのプロンプト生成
- 並列画像生成
- PPTX/PDF エクスポート

### v1
- 初期実装
- SubAgent ベースの Markdown 分析
