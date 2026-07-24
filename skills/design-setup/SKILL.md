---
name: design-setup
description: "TEKION Slide Generator v6 のブランドデザインを設定するオンボーディングスキル。初めて使う会社が自社のデザインフォーマット（ロゴ・ロゴ位置・配色・フォント・トーン）を登録できる。素材ファースト: ロゴ画像、スライドマスターの PPTX、ブランドガイドラインのスクショ、既存デッキなどを受け取って解釈し、プリセット・ロゴ・設定ファイルを適切な場所に配置する。既存プリセットの改善（ロゴ位置変更・色修正など）にも対応。「デザインを設定したい」「ブランドをカスタマイズしたい」「自社カラーに設定して」「ロゴを設定して/位置を変えて」「この会社用のフォーマットを作って」で発動。"
---

# TEKION Slide Generator — デザインセットアップ（v6 オンボーディング）

自社ブランドのプリセットを作り、次回以降の「スライドを作って」に自動適用させる。
方針: **素材をもらって解釈する**。質問攻めにせず、ユーザーが持っているものから最大限読み取る。

## 定数

```bash
DESIGN_SKILL_DIR="<このSKILL.mdのディレクトリ>"
V6_SKILL_DIR="${DESIGN_SKILL_DIR}/../tekion-slide-generator-v6"
# プリセットはユーザーホームに置く（プラグイン更新でスキルフォルダが入れ替わっても消えない）
PRESETS_DIR="${TEKION_PRESETS_DIR:-$HOME/.tekion-slides/presets}"
mkdir -p "${PRESETS_DIR}/assets"
BRAND_ASSETS_DIR="${PRESETS_DIR}/assets"        # プリセット毎のロゴ置き場: assets/<slug>/logo.png
TEMPLATE_FILE="${V6_SKILL_DIR}/references/presets/example-preset.md"  # 同梱テンプレート（読み取りのみ）
```

成果物（ブランド1社ぶん）:

| ファイル | 内容 |
|---|---|
| `${PRESETS_DIR}/<slug>.md` | デザインガイドライン（配色・フォント・トーン・レイアウトパターン） |
| `${BRAND_ASSETS_DIR}/<slug>/logo.png` | ロゴ（透過PNG推奨） |
| `${PRESETS_DIR}/<slug>.config.json` | 機械可読設定: ロゴパス・位置・スケール・フッター |
| `${PRESETS_DIR}/.active_preset` / `.active_style` | アクティブ切替 |

config.json のスキーマ（v6 の `scripts/resolve_brand.py` が読む）:

```json
{
  "logo": "assets/<slug>/logo.png",
  "logo_position": "bottom-right",
  "logo_scale": 0.09,
  "footer_text": "©2026 Example Inc."
}
```

- `logo_position`: bottom-right / bottom-left / top-right / top-left
- `logo_scale`: 画像幅に対する比率 0.02〜0.30（デフォルト 0.09）
- `footer_text`: 省略 = デフォルト透かし。`""` = フッター無し

## モード判定（最初にやる）

`.active_preset` と既存プリセット一覧を表示し、要望からモードを選ぶ:

1. **切替** — 既存プリセットを選ぶだけ → `.active_preset` / `.active_style` を書いて完了
2. **新規オンボーディング** — 新しい会社/ブランドの登録 → 下の手順へ
3. **改善登録** — アクティブプリセットへの調整（「ロゴを左下に」「Primaryをもう少し暗く」等）
   → 該当ファイル（config.json / <slug>.md / logo.png）だけ直して完了。変更後は下の「反映テスト」を提案

## 新規オンボーディング手順

### 1. ブランド名と素材を一度に聞く

> 「会社名・ブランド名を教えてください。あわせて、手元にあるブランド素材があれば何でも渡してください
> （どれか1つでも、全部でも、無くてもOK）:
> ・ロゴ画像（PNG推奨）
> ・スライドマスター/テンプレートの PowerPoint（.pptx / .potx）
> ・ブランドガイドラインの PDF やスクリーンショット
> ・普段使っている資料のスクショや PPTX
> ・コーポレートサイトの URL」

slug はスクリプトで決定的に生成する: `python3 "${DESIGN_SKILL_DIR}/scripts/slugify.py" "<ブランド名>"`
同名プリセットがあれば上書きか別名かを確認する。

### 2. 素材を解釈する（もらったものに応じて）

- **PPTX / POTX** — テーマ色・フォント・ロゴ候補を一括抽出:
  ```bash
  python3 "${DESIGN_SKILL_DIR}/scripts/analyze_pptx_assets.py" \
    --file <入力.pptx> --extract-dir "${BRAND_ASSETS_DIR}/<slug>/extracted"
  ```
  出力 JSON の `theme.colors`（accent1 が Primary 候補）、`theme.fonts`、`logo_candidates` を使う。
  ロゴ候補は必ず Read で目視し、どれがロゴかを自分で判断してからユーザーに確認する。
- **スクショ / 画像 / PDF** — Read で目視し、Primary・アクセント色（hex 目視推定）、フォントの雰囲気、
  レイアウト傾向（ロゴの定位置、余白、装飾）を抽出する
- **ロゴ画像** — Read で目視して主要色を1-3個推定（Primary/Accent 候補）
- **URL** — WebFetch で取得し、ブランドカラーやトーンを読み取る
- **素材なし** — 対話で聞く: Primary色（言葉でもOK）→ フォント雰囲気（丸ゴシック/シャープ/セリフ/細身）→
  トーン（1文）。それ以外はデフォルト採用

Primary が決まったら派生色を決定的に算出する:

```bash
python3 "${DESIGN_SKILL_DIR}/scripts/derive_palette.py" "<PRIMARY_HEX>"
# → primary_light（淡背景・黒テキスト前提）/ primary_dark（濃背景・白テキスト前提）
```

Accent Teal / Gold、セマンティック、グレースケールは example-preset.md のデフォルトを使い、
素材から明確に読み取れた場合のみ差し替える。

### 3. ロゴを配置し、位置を決める

```bash
mkdir -p "${BRAND_ASSETS_DIR}/<slug>"
cp <ロゴファイル> "${BRAND_ASSETS_DIR}/<slug>/logo.png"
```

- チルダはクォート内で展開されない。`${path/#\~/$HOME}` で展開してから存在確認する
- ロゴ位置は素材（既存資料のスクショ等）から読み取れたらそれを提案、なければ右下をデフォルト提案
- 白ロゴ（暗背景用）しか無い場合はその旨を伝え、視認性の注意を preset md の禁止事項に書く

### 4. サマリー確認 → 書き出し

抽出結果（Primary/派生色/アクセント/フォント/トーン/ロゴ位置/フッター）を1画面のサマリーで見せ、
OK をもらってから書き出す:

1. `<slug>.md` — `${TEMPLATE_FILE}` をベースに、配色 hex・フォント・トーンを置換。
   レイアウトパターン・図解パターンはテンプレートのまま流用（色参照だけ置換）
2. `<slug>.config.json` — 上のスキーマで作成
3. `.active_preset` に `<slug>.md`、`.active_style` に balanced（希望があれば visual）を書く

### 5. 反映テスト（推奨）

サブスク枠を消費しない mock で、ロゴ位置・スケールの合成だけ即時確認できる:

```bash
eval "$(python3 "${V6_SKILL_DIR}/scripts/resolve_brand.py")"
python3 "${V6_SKILL_DIR}/scripts/generate_slide_with_retry.py" \
  --provider mock --prompt "test" --output /tmp/brand_test.png --logo "${LOGO}"
```

生成された画像を Read で目視し、ユーザーにも見せて確認する。
本番品質の1枚テスト（codex・サブスク枠消費）を望むなら v6 スキルの Phase 3〜7 を1枚だけ流す。

## 完了メッセージ

生成/更新したファイル一覧と「次回『スライドを作って』でこのブランド設定が自動適用される」ことを伝える。
調整したくなったら「ロゴを左下にして」「Primaryを変えて」のように言えば改善登録モードで直せることも添える。

## 知見

- 質問は素材で代替できるものから削る。全部デフォルトでも動くので、確認は「サマリー1回」に集約する
- PPTX のテーマ色 accent1 は Primary と一致しないことがある（テンプレ配布元の色が残っているケース）。
  スライド実物のスクショや実データの色と食い違ったら実物を優先する
- ロゴの自動スコアリングは候補出しまで。**必ず Read で目視してから** ユーザーに提示する
- v6 のロゴ合成は生成後の PIL 合成（`providers/codex.py::_composite_logo`）。位置・スケールは
  環境変数 `SLIDE_LOGO_POSITION` / `SLIDE_LOGO_SCALE` で制御され、`resolve_brand.py` が
  config.json から設定する。プロンプトにロゴ指示を書く必要はない
