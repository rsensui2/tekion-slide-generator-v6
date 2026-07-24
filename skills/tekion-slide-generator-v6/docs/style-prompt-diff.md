# Visual vs Balanced — プロンプト設計の違い

## 🎯 TL;DR

| 項目 | Balanced | Visual |
|------|---|---|
| 哲学 | Pitch.com / Figma Slides 的な洗練された営業資料 | Keynote / TED 的な余白美学 |
| タイトル文字数 | 制限なし | **15文字以内** |
| 本文 | 見出し + 3-5項目の箇条書き可 | **1-2行のみ** |
| 箇条書き項目上限 | 5-6項目 | **3項目未満**（4項目以上は禁止） |
| 並列カード/ボックス | OK（2-3個） | **禁止** |
| ビジュアル画面占有率 | 40-60% | **60-80%** |
| 余白 | 適度 | **大胆**（30%以上OK） |
| content内の詳細図解指示 | 尊重 | **無視して核だけ抽出** |
| フルブリード写真 | 使用可 | **積極推奨** |
| 想定用途 | 営業資料・提案書・配布資料 | 登壇・ピッチ・キービジュアル |

---

## 📜 テンプレートの主要ブロック差分

### 1. `style` セクション（設計哲学）

#### Balanced
```yaml
style:
  name: "Balanced"
  philosophy: "Pitch.com / MorningBrew / Figma Slides のような洗練されたビジネススライド"
  text_volume: "見出し + 3-5項目の簡潔な箇条書き、または2-3ブロックの図解"
  visual_ratio: "テキスト40-60% / ビジュアル40-60%のバランス"
  layout: "2-3要素のグリッド、明確な階層、適度な余白"
  tone: "プロフェッショナルで信頼感のある、営業・提案の場面に適した品格"
```

#### Visual
```yaml
style:
  name: "Visual"
  philosophy: "Apple Keynote / TED Talk / Dieter Rams の余白美学。Less is more."
  text_volume: "極限まで少なく。タイトル15文字以内 + キーメッセージ1文（最大2行）。それ以外のテキストは原則なし"
  visual_dominance: "画面の60-80%を1つの大胆なビジュアル（全面写真・巨大数字・大きなシンボル・印象的なイラスト）が占める"
  layout: "大胆な余白、中央集中または黄金比の片寄せ。1スライド1メッセージ"
  emotional_impact: "見た瞬間に1つのメッセージが伝わる。読ませるのではなく、感じさせる"
```

**違い**: Visual には `emotional_impact`（感情的インパクト）という軸が明示される。情報伝達よりも「感じさせる」ことを優先。

---

### 2. `constraints` セクション（許可/禁止）

#### Balanced
```yaml
constraints:
  editorial_discipline:
    - "情報をすべて詰め込むのではなく、核となる要点に絞る"
    - "箇条書きは最大5項目まで、それ以上は図解化するかカットする"
    - "1スライドに伝えたいメッセージは1つに集中させる"
  visual_quality:
    - "図解・アイコン・イラスト・写真のいずれかを取り入れる（文字だけにしない）"
    - "カード・ボックスは必要に応じて使用（強制ではない）"  ← 強制しない
    - "余白を恐れず、読みやすさを優先"
```

#### Visual
```yaml
constraints:
  forbidden_text_in_image:
    - "本文の全文再現（contentに書かれていても、必要な核だけ抽出）"
    - "複数の箇条書き（3項目以上の並列リストは禁止）"     ← 強く禁止
    - "複数のカード・ボックスに情報を並列配置"              ← 強く禁止
    - "詳細な説明文・補足・注釈の複数行表示"              ← 強く禁止
  visual_requirements:
    - "画面の主役は1つのビジュアル要素（写真・巨大数字・アイコン・イラスト・シンボル）"
    - "フルブリード（端から端まで）の写真・グラデーション・イラストを積極的に使用"
    - "余白を恐れない。画面の30%以上を意図的に空白にして良い"
    - "テキストは呼吸する余白の中に配置する"
```

**違い**: Visualは「複数項目・カード・補足」を**明示的に禁止**している。Balancedは「できれば絞る」程度のソフトな指示。

---

### 3. **content の扱い** が決定的に異なる

#### Balanced
JSON の `content` フィールド内の図解指示（`<!-- Pattern X -->` など）を **そのまま尊重**して実装:
```yaml
design:
  layout_approach: "コンテンツ内容に応じて最適な構成を選択。Design Guidelines の Pattern A-K を参考に..."
```

#### Visual
**content内の詳細指示を無視するよう明示的に命令**:
```yaml
important_note_about_content: |
  上記 content に詳細な図解指示・箇条書きが含まれていても、
  Visualスタイルでは**その通りに全部描画しないこと**。
  content から核となる1メッセージ・核となる1ビジュアル要素のみを抽出し、
  余白を活かした大胆な構成で表現すること。
  詳細な図解・複数カード・網羅的な箇条書きは **Visualスタイルでは禁止**。
```

→ これにより、**同じJSON** でもVisualだと勝手に削ぎ落としてくれる。

---

### 4. `design_direction`（本編スライドの構成指示）

#### Balanced
```yaml
selection_hints:
  比較・対比: "左右スプリット"
  手順・フロー: "横または縦のフロー図（3-5ステップ程度）"
  リスト・並列項目: "2-3列のカードまたは箇条書き"
  強調・数字: "大きな数字＋ラベル、1-3個までに絞る"
  プロフィール・人物: "左右スプリット（写真+テキスト）"
  時系列: "タイムライン"
```

#### Visual
```yaml
visual_patterns:
  - "フルブリード写真 + 中央にキャッチコピー1行"
  - "巨大数字（画面の30-40%）+ 短いラベル"
  - "大きなシンボル/アイコン（画面の20-30%）+ タイトル+1文"
  - "左右分割: 左に大きな写真、右に短い見出し+1-2行のみ"
  - "印象的なイラストまたはメタファー画像 + タイトル"
what_to_cut:
  - "contentに書かれた複数項目の箇条書き → 最も重要な1点に絞る"
  - "図解の詳細指示（Pattern X、カード構造等） → 無視して、代わりに1つのビジュアル要素を大胆に"
  - "補足説明・注釈 → カット or 極小フッターに"
```

**違い**: Visual は「**何を切り捨てるか**」を明示的に指示。Balanced は「どう組むか」の指示のみ。

---

## 🔍 実プロンプトの差分（00_cover の例）

### Balanced 版（要約）
```
あなたはプレゼンテーションスライドのデザイナーです。
洗練された営業資料・提案書スタイルの高品質な1枚スライドを生成してください。

style:
  name: "Balanced"
  philosophy: "Pitch.com..."
  text_volume: "見出し + 3-5項目の簡潔な箇条書き..."

[Content Data]
title: "VibeCoder Bootcamp"
subtitle: "超実践型..."
key_message: "AIを完全に味方につける、1.5ヶ月。"
content: |
  <!-- Pattern G: インパクト重視の表紙 -->
  ...3つのタグライン...
  背景: 白ベース。幾何学ライン...
  アクセント: タイトル下にPrimary色の短い横線...

design:
  layout: "表紙レイアウト"
  structure: "タイトル（大）、サブタイトル（中）、補足（小）の3層"
```

### Visual 版（要約）
```
あなたは世界最高レベルのプレゼンテーションデザイナーです。
Apple Keynote や TED Talk のような、極限まで洗練されたピッチデッキを1枚生成してください。

style:
  name: "Visual"
  philosophy: "Apple Keynote / TED Talk / Dieter Rams の余白美学。Less is more."
  text_volume: "極限まで少なく..."
  emotional_impact: "見た瞬間に1つのメッセージが伝わる..."

[Content Data]
title: "VibeCoder Bootcamp"
subtitle: "超実践型..."
content: |
  （Balancedと同じJSON内容）

important_note_about_content: |
  上記 content に詳細な図解指示・箇条書きが含まれていても、
  **その通りに全部描画しないこと**。核だけ抽出...

design_direction:
  layout: "フルブリード表紙"
  composition: "極大タイトル（画面の30-50%）+ サブタイトル1行のみ。
               背景は大胆な全面写真またはグラデーション"
```

---

## 🎨 「デザインが今ひとつ」問題の分析

ご指摘の通り、現状の Visual は「**情報は削れたが、デザイン的なパンチが弱い**」状態。原因と改善案：

### 原因

1. **「大胆」「印象的」という指示が抽象的すぎる**
   - `"大胆な全面写真"` と書いても、モデルはデフォルトの無難な装飾に流れる
   - 具体的なムード・質感・世界観の指示がない

2. **参考デザインの例示が不足**
   - 「Keynote風」「TED風」と書いても、モデルが想起するのは汎用的なイメージ
   - 具体的に「2010年代Apple基調講演のスライド」「Pitch.com Series Bピッチ」のような固有名詞レベルが効く

3. **感情・ブランドトーンの指示が弱い**
   - ブランド固有のムード（例:「情熱的でありながら実直」「オレンジ=行動のエネルギー」）を明示すべき
   - 単に「Primary色で」では「ただのオレンジ塗り」になる

4. **演出レイヤの指示がない**
   - タイポグラフィ効果（グロー/オーバーレイ/ミックス）
   - 写真の処理（グレーディング/デュオトーン/モーションブラー）
   - 背景の演出（光の粒子/幾何学/グラデーションメッシュ）

### 改善案（Visual v2 への進化）

#### A. `mood` / `vibe` セクションを追加
```yaml
mood:
  keywords: ["energetic", "confident", "cutting-edge", "human-warm"]
  color_energy: "Primary色(#EA5514)を熱量・情熱のメタファーとして大胆に使う"
  tone: "エリート感と親しみやすさの両立。近未来的だが冷たくない"
```

#### B. 参考デザインの固有名詞化
```yaml
reference_design:
  inspired_by:
    - "Apple Keynote（2015-2020年代の基調講演スライド）"
    - "Stripe Sessions のメインビジュアル"
    - "Figma Config のスピーカースライド"
    - "Linear.app のランディングページ"
  typography_mood: "SF Pro Display / Inter のような現代的サンセリフの極太 + 広い字間"
  imagery_mood: "Unsplash editorial、Pexels cinematic のような、光と陰影のドラマ"
```

#### C. 演出効果の明示
```yaml
visual_effects:
  allowed:
    - "グラデーションメッシュ・グラデーションブラー"
    - "大胆なタイポグラフィ配置（画面端を超えるカット/下揃え/ベースライン演出）"
    - "写真のデュオトーン処理（Primary色 x Gray-900 の2色分解）"
    - "光の粒子・抽象的な幾何学シンボル（ただし1スライド1要素まで）"
  discouraged:
    - "ベタ塗りの単色背景（質感のない塗り）"
    - "ステレオタイプなIT系アイコン（歯車・クラウド・コード）"
```

#### D. `title_treatment`（タイトルの扱い）を専用指示化
```yaml
title_treatment:
  size: "画面高さの20-35%を占める極大"
  weight: "極太（Black 900）"
  positioning: "中央ではなく、画面の黄金比ポイント（左下3分の1など）に意図的に配置可"
  interaction_with_bg: "背景のビジュアルと重ねる/マスクする/部分的に切り抜くなど、レイヤリングで立体感"
```

---

## 💡 次のステップ（提案）

ユーザーのご意向を伺いたい分岐：

1. **Visual v2 を作る** — 上記 A-D の改善を反映した `prompt_template_visual_v2.j2` を新設
   - 既存 `visual` はシンプル版、`visual_v2`（または `visual_pro`）は演出強化版
2. **visualテンプレを直接改修** — 既存の `visual` をパワーアップさせる
3. **design_guidelines.md 側を拡張** — テンプレではなくプロジェクト固有のデザインガイドラインに mood/reference を書く
4. **今のまま運用** — デザインパンチより情報コントロールを優先

**推奨**: **2（既存 visual を改修）+ 3（design guidelinesにブランドムード追加）のハイブリッド**。
テンプレに「演出の枠組み」、design guidelinesに「このブランドはこうありたい」というトーンを記述するのが役割分担として健全。

---

## 📁 関連ファイル

- [templates/prompt_template_balanced.j2](../templates/prompt_template_balanced.j2)
- [templates/prompt_template_visual.j2](../templates/prompt_template_visual.j2)
- [scripts/generate_prompts_from_json.py](../scripts/generate_prompts_from_json.py) — `--style` / `_style` 処理
- [SKILL.md](../SKILL.md) — スタイル選択セクション
