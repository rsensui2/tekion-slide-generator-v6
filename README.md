# TEKION Slide Generator — 「全枚数、確実に、同じ顔で。」

**Markdown / テキスト → 日本語 16:9 スライド → ブラウザ校正室でレビュー → PPTX / PDF** を、
Claude Code でも Codex でも動かせるプラグイン。既存 PPTX / PDF の取り込み改修にも対応。

画像生成は **Codex 内蔵 gpt-image-2（ChatGPT サブスク枠）** — API キー・従量課金なしで動く。

## 特徴

| 柱 | 内容 |
|---|---|
| 校正室 | 生成実況・スライド毎の赤入れ→ワンクリック修正依頼・バージョン比較（選択=確定）・PPTX/PDFダウンロードを1画面で |
| ゼロ欠損保証 | manifest + 生成後の機械検証 + 検証スイープ + resume。「N枚頼んだらN枚返る」 |
| フルスロットル並列 | 枚数ぶん一斉ファンアウト（上限20）。429検知で AIMD 自動減速 |
| 直せる | 差分編集・赤ペン編集・ロールバック。既存 PPTX/PDF もドロップで取り込んで改修 |

## インストール

### 前提

- Python 3.10+（依存は初回に `setup.sh` が自動導入: Pillow / python-pptx / requests / Jinja2 / pymupdf）
- [Codex CLI](https://developers.openai.com/codex/) + `codex login` 済み（ChatGPT アカウント）
- ネイティブ PPTX の取り込みに LibreOffice（任意）

### Claude Code

```bash
# GitHub から（公開後）
/plugin marketplace add rsensui2/tekion-slide-generator
/plugin install tekion-slide-generator@tekion-slide-generator

# または ローカルパスから
/plugin marketplace add /path/to/tekion-slide-generator
/plugin install tekion-slide-generator@tekion-slide-generator
```

### Codex

```bash
# GitHub から（公開後）
codex plugin marketplace add rsensui2/tekion-slide-generator
codex plugin add tekion-slide-generator

# またはスキルを直接コピー
cp -r skills/tekion-slide-generator-v6 ~/.codex/skills/
```

## 使い方

インストール後、エージェントにそのまま話しかける:

- 「このMarkdownからスライドを作って」 → 生成 → 校正室が開いて実況 → 赤入れ → 書き出し
- 「このパワポ（PPTX/PDF）を直したい」 → 校正室にドロップ → 1枚ずつ分解 → 赤入れ → 差分編集
- 校正室だけ開く: `python3 skills/tekion-slide-generator-v6/scripts/review_deck.py --session-dir <dir> --serve`

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
        ├── scripts/                 # 生成・校正室・取り込み・export（Python）
        ├── templates/               # プロンプトテンプレート（Jinja2）
        ├── references/              # デザインガイドライン・プリセット
        └── assets/                  # ロゴ・UI素材
```

## ライセンス

公開（ソース公開）だが OSS ではない。未改変利用は可（商用含む）、改変・再配布は事前許可制。
© TEKION Group — [tekion.jp](https://tekion.jp) · [VibeCoder Bootcamp](https://vibe-coder-bootcamp.com) · [ai-agent.co.jp](https://ai-agent.co.jp)
