# 🎨 Branding Guide — 自社ブランドを反映するには

slide-generator を自社向けにカスタマイズする完全ガイド。
30分程度で「自社の資料テンプレ」として運用開始できます。

---

## 全体像：3 レイヤの責務

| レイヤ | 何を設定 | ファイル |
|-------|---------|---------|
| **1. Asset 層** | ロゴ・ブランド画像 | `assets/logo.png`, 参考画像 |
| **2. Guideline 層** | 配色・フォント・レイアウト | `references/presets/*.md` |
| **3. Template 層**（上級） | 演出指示・ムード・デザイン哲学 | `templates/*.j2` |

初めての方は **Asset → Guideline** の順で手を入れれば十分です。

---

## Step 1: ロゴ差し替え

### ファイル置き換え

```bash
# 既存のサンプルを退避してから上書き
mv ~/.claude/skills/tekion-slide-generator-v5/assets/logo.png \
   ~/.claude/skills/tekion-slide-generator-v5/assets/logo.png.sample
cp ~/Downloads/mycompany-logo.png \
   ~/.claude/skills/tekion-slide-generator-v5/assets/logo.png
```

### 推奨仕様

| 項目 | 推奨値 |
|------|--------|
| 形式 | PNG（透過可） / JPEG / WebP |
| 解像度 | 1200px 以上（幅） |
| アスペクト比 | 横長（3:1 〜 6:1） |
| 背景 | 透過 or 白 |
| 色 | モノクロ or ブランドカラー1-2色 |

### 動作

- `--logo` オプションでスライド生成時に自動配置
- 右下フッターに自動縮小配置
- **ピクセル忠実度を維持** するよう、プロンプトに明示指示が入っている

---

## Step 2: デザインガイドライン

### 2.1 プリセットをコピー

```bash
cd ~/.claude/skills/tekion-slide-generator-v5
cp references/presets/example-preset.md references/presets/mycompany.md
```

### 2.2 配色パレット（必須）

`mycompany.md` を開いて配色を書き換える:

```markdown
## 配色パレット

### ブランドカラー（Primary）
- Primary: #2563EB       ← あなたのブランドメインカラー
- Primary Light: #DBEAFE ← 薄い背景色
- Primary Dark: #1E40AF  ← 強調・高コントラスト用

### アクセントカラー
- Accent Teal: #14B8A6   ← データ可視化の第2系列
- Accent Gold: #EAB308   ← 第3系列・注意

### セマンティックカラー
- Success: #16A34A
- Warning: #EAB308
- Error:   #DC2626
- Info:    #2563EB

### グレースケール
- Gray-900: #0F172A  ← 見出し
- Gray-700: #334155  ← 本文
- Gray-400: #94A3B8  ← 補足
- Gray-200: #E2E8F0  ← 罫線
- Gray-100: #F1F5F9  ← カード背景
```

### 2.3 フォント

```markdown
## フォント
- 見出し: 太字の丸みのあるゴシック系
- 本文: 丸みのあるゴシック系

### トーン
- プロフェッショナルで信頼感のある
- モダンで先進的
- 余白を活かした読みやすさ重視
```

モデルは具体的なフォント名を再現できないことが多いので、**雰囲気ワード**（「丸み」「先進的」「厳格」等）で伝える方が効く。

### 2.4 レイアウトパターン

Pattern A-K の使い分けを自社文脈で定義:

```markdown
## レイアウトパターン

### Pattern A: スプリット
用途: 比較（Before/After）、2つの概念
構造: 左50%テキスト + 右50%ビジュアル

### Pattern B: プロセス・フロー
用途: 手順、時系列、サイクル
（...以下同様にA-K）
```

### 2.5 ブランド固有のムード（任意だが効く）

```markdown
## ブランドムード

### キーワード
- energetic / confident / cutting-edge / human-warm

### 使用ルール
- Primary色は「行動のエネルギー」のメタファー。大胆に使って良い
- 60-30-10 ルール: 60%ニュートラル、30%Primary Light、10%Primary

### 参考デザイン（AI向けヒント）
- Apple Keynote（2015-2020年代の基調講演スライド）
- Stripe Sessions のメインビジュアル
- Figma Config のスピーカースライド
```

参考デザインを **固有名詞** で書くと、モデルがそのスタイルを想起しやすくなります。

### 2.6 禁止事項

```markdown
## 禁止事項
- ステレオタイプなIT系アイコン（歯車・クラウドの汎用イメージ）
- ベタ塗り単色の背景（質感のない塗り）
- 他社ブランドを想起させる色（競合の青/赤など）
- カラーコード文字列の画像内描画
```

---

## Step 3: セッションに適用

```bash
# 新規セッション作成時
SESSION=~/Desktop/slides_output/my_project
mkdir -p ${SESSION}/{json,prompts,images}

# 自社プリセットをコピー
cp ~/.claude/skills/tekion-slide-generator-v5/references/presets/mycompany.md \
   ${SESSION}/design_guidelines.md

# あとは通常のフロー（スキル経由なら自動）
```

---

## 上級: プロンプトテンプレートを Fork する

### いつ必要か

- Guideline だけでは「演出の質」が足りない
- 自社の**特定の世界観**を徹底的に反映したい
- AIの画像生成のクセを更に制御したい

### 手順

```bash
cd ~/.claude/skills/tekion-slide-generator-v5/templates

# Balanced版をベースにFork
cp prompt_template_balanced.j2 prompt_template_mycompany.j2
```

### 追加推奨セクション

```jinja2
mood:
  keywords: ["energetic", "confident", "cutting-edge", "human-warm"]
  color_energy: "Primary色を熱量・情熱のメタファーとして大胆に使う"
  tone: "エリート感と親しみやすさの両立"

reference_design:
  inspired_by:
    - "Apple Keynote（2015-2020年代の基調講演スライド）"
    - "Stripe Sessions のメインビジュアル"
    - "Linear.app のランディングページ"
  typography_mood: "SF Pro Display / Inter のような現代的サンセリフの極太 + 広い字間"
  imagery_mood: "Unsplash editorial, Pexels cinematic のような光と陰影のドラマ"

visual_effects:
  allowed:
    - "グラデーションメッシュ・グラデーションブラー"
    - "大胆なタイポグラフィ配置（画面端を超えるカット）"
    - "写真のデュオトーン処理（Primary色 x Gray-900）"
  discouraged:
    - "ステレオタイプなIT系アイコン"
    - "ベタ塗り単色の背景"

title_treatment:
  size: "画面高さの20-35%を占める極大"
  weight: "Black 900"
  positioning: "黄金比ポイント（左下3分の1など）配置も可"
```

### 使用

```bash
python3 scripts/generate_prompts_from_json.py \
  --session-dir ${SESSION} \
  --template-path templates/prompt_template_mycompany.j2
```

---

## チェックリスト

自社ブランド反映が完了したかの確認:

- [ ] `assets/logo.png` が自社ロゴに差し替え済み
- [ ] `references/presets/mycompany.md` に自社の配色・トーンが反映済み
- [ ] 試しに 3-5枚生成し、期待通りのルック＆フィールか確認
- [ ] 日本語テキスト精度が十分か確認
- [ ] 営業/登壇/講義いずれの用途でも再現可能か確認
- [ ] チームメンバーにテンプレ運用手順を共有

---

## 参考

- [style-prompt-diff.md](style-prompt-diff.md) — Visual vs Balanced の詳細比較
- [skill-guide.md](skill-guide.md) — スキル全体の詳細ガイド
- [architecture.md](../references/architecture.md) — アーキテクチャ詳細
