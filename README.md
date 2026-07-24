# TEKION Slide Generator — 「全枚数、確実に、同じ顔で。」

**Markdown / テキスト → 日本語 16:9 スライド → ブラウザスライドダッシュボードでレビュー → PPTX / PDF** を、
Claude Code でも Codex でも動かせるプラグイン。既存 PPTX / PDF の取り込み改修にも対応。

画像生成は **Codex 内蔵 gpt-image-2（ChatGPT サブスク枠）** — API キー・従量課金なしで動く。

## 特徴

| 柱 | 内容 |
|---|---|
| スライドダッシュボード | 生成実況・スライド毎の赤入れ→ワンクリック修正依頼・バージョン比較（選択=確定）・PPTX/PDFダウンロードを1画面で |
| ゼロ欠損保証 | manifest + 生成後の機械検証 + 検証スイープ + resume。「N枚頼んだらN枚返る」 |
| フルスロットル並列 | 枚数ぶん一斉ファンアウト（上限20）。429検知で AIMD 自動減速 |
| 直せる | 差分編集・赤ペン編集・ロールバック。既存 PPTX/PDF もドロップで取り込んで改修 |

## インストール

### 初回のみ: 常駐ハブのセットアップ

ダッシュボードは固定URL `http://127.0.0.1:7799/` の常駐ハブで動く（修正指示の自動処理・
タブの自動更新・全セッション一覧はハブの機能）。初回に1度だけ:

```bash
bash skills/tekion-slide-generator-v6/scripts/install_hub.sh   # macOS: launchd に常駐登録
```

> 🎓 **受講生に配る場合**: [docs/INSTALL_PROMPT.md](docs/INSTALL_PROMPT.md) のプロンプトを
> Claude Code / Codex に貼り付けてもらうだけで、エージェントがインストールから使い方の案内まで行う。

### 前提

- Python 3.10+（依存は初回に `setup.sh` が自動導入: Pillow / python-pptx / requests / Jinja2 / pymupdf）
- [Codex CLI](https://developers.openai.com/codex/) + `codex login` 済み（ChatGPT アカウント）
- ネイティブ PPTX の取り込みに LibreOffice（任意）

### Claude Code

```bash
# GitHub から
/plugin marketplace add rsensui2/tekion-slide-generator-v6
/plugin install tekion-slide-generator@tekion-slide-generator

# または ローカルパスから
/plugin marketplace add /path/to/tekion-slide-generator
/plugin install tekion-slide-generator@tekion-slide-generator
```

### Codex

```bash
# GitHub から
codex plugin marketplace add rsensui2/tekion-slide-generator-v6
codex plugin add tekion-slide-generator@tekion-slide-generator

# またはスキルを直接コピー
cp -r skills/tekion-slide-generator-v6 ~/.codex/skills/
```

## 使い方（3ステップ）

インストール後、エージェント（Claude Code / Codex）にそのまま話しかける:

1. **作る / 読み込む** — 「◯◯のスライドを作って」。既存の PPTX/PDF を直すならスライドダッシュボードにドロップ
2. **赤入れで直す** — ブラウザに開くダッシュボードで、直したいスライドの赤い記入欄に指示を書いて ⏎。AI が該当スライドだけ描き直し、版を比較して選べる
3. **持っていく** — 右上の ⤓ PPTX / ⤓ PDF でダウンロード

ダッシュボードだけ開く: `python3 skills/tekion-slide-generator-v6/scripts/review_deck.py --session-dir <dir> --serve`

## 自社デザインへのカスタマイズ（design-setup 同梱）

エージェントに **「デザインを設定したい」** と言うだけで、対話形式のオンボーディングが始まる:

- **素材ファースト**: ロゴ画像・スライドマスターの PPTX・ブランドガイドや既存資料のスクショ・
  コーポレートサイト URL — 手元にあるものを渡すと解釈して配色・フォント・ロゴ位置を抽出
- **質問に答えるだけでも OK**: 素材が無ければメインカラーとトーンを聞かれる程度
- 作ったプリセットは `~/.tekion-slides/presets/` に保存され（**プラグインを更新しても消えない**）、
  次回から「スライドを作って」だけで自社デザインが自動適用される
- 微調整は「ロゴを左下に」「メインカラーをもう少し暗く」のように言えば反映される
- **キャラクター・写真の登録**: 「キャラクターを登録したい」と言って画像を渡すと
  `~/.tekion-slides/assets/` のアセットライブラリに保存され、以後「◯◯を出して」と
  言うだけで該当スライドの参照画像として自動で使われる

詳細な実行手順は [skills/tekion-slide-generator-v6/SKILL.md](skills/tekion-slide-generator-v6/SKILL.md)。

## 構成

```
tekion-slide-generator/
├── .claude-plugin/          # Claude Code プラグイン + マーケットプレイス定義
├── .codex-plugin/           # Codex プラグイン定義
├── .agents/plugins/         # Codex マーケットプレイス定義
└── skills/
    └── tekion-slide-generator-v6/   # 本体（両エージェント共通）
        ├── SKILL.md                 # 実行手順（Phase 1〜9）
        ├── scripts/                 # 生成・スライドダッシュボード・取り込み・export（Python）
        ├── templates/               # プロンプトテンプレート（Jinja2）
        ├── references/              # デザインガイドライン・プリセット
        └── assets/                  # ロゴ・UI素材
```

## ライセンス

公開（ソース公開）だが OSS ではない。未改変利用は可（商用含む）、改変・再配布は事前許可制。
© TEKION Group — [tekion.jp](https://tekion.jp) · [VibeCoder Bootcamp](https://vibe-coder-bootcamp.com) · [ai-agent.co.jp](https://ai-agent.co.jp)
