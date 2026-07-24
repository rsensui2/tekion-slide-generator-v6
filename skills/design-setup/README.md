# design-setup

TEKION Slide Generator v4 のブランドデザインを対話形式でカスタマイズするコンパニオンスキル。

ロゴ・配色・フォント・トーン・ブランドムード・禁止事項を 1 問ずつ会話で設定し、
`tekion-slide-generator-v4/references/presets/` 配下に再利用可能なプリセットファイルを生成する。
生成されたプリセットは次回のスライド生成に自動適用される。

## インストール

`tekion-slide-generator-v4` と同階層に配置する：

```bash
# macOS / Linux / WSL
cp -r design-setup/ ~/.claude/skills/design-setup/

# Windows (PowerShell)
Copy-Item -Recurse design-setup $env:USERPROFILE\.claude\skills\design-setup
```

`~/.claude/skills/` 配下のレイアウトは以下になっている前提：

```
~/.claude/skills/
├── design-setup/                    ← このスキル
└── tekion-slide-generator-v4/       ← 必須。事前にインストール
```

## 起動

Claude Code で以下のいずれかを発話すると起動する：

- 「デザインを設定したい」
- 「ブランドをカスタマイズしたい」
- 「テーマを変えたい」
- 「自社カラーに設定して」
- 「ロゴを設定して」

## 動作環境

| 環境 | 対応 |
|------|:---:|
| macOS | ✅ |
| Linux | ✅ |
| Windows (WSL2) | ✅ |
| Windows (Git Bash) | ⚠️ Python が PATH 上にあれば動作 |
| Windows (PowerShell 単体) | ❌ 非対応 |

**理由**: SKILL.md 内の処理は POSIX シェル (bash) を前提にしている。Claude Code は内部的に bash を起動するため、Windows でも WSL または Git Bash 上で実行する必要がある。

### 必須要件

- **bash 環境** (macOS / Linux 標準、Windows は WSL または Git Bash)
- **Python 3.6 以降** (`python3` または `python` コマンドが PATH 上で利用可能)
  - スキルは起動時に `command -v python3 || python` で自動検出する
- **tekion-slide-generator-v4 スキル** が同階層にインストール済みであること

## 中で何が起きるか

1. `Step 0`: 既存プリセットの確認、新規作成 or 既存選択を選ぶ
2. `Step 1-12`: ブランド名 / ロゴ / 配色 / フォント / トーン / 使用ルール / 参考デザイン / 禁止事項 / デフォルトスタイル を順に対話で決める
   - ロゴ指定時は画像から主要色を抽出して Primary 候補として提示
   - 過去スライド画像を渡すと共通パターンを分析してルール化
3. `サマリー → プリセット生成`: `references/presets/{slug}.md` に書き出し、`.active_preset` / `.active_style` に記録
4. 完了後、`tekion-slide-generator-v4` で「スライドを作って」と言えば自動でこのプリセットが適用される

## 含まれるファイル

```
design-setup/
├── README.md                    本書
├── SKILL.md                     スキル本体 (対話フロー)
└── scripts/
    ├── derive_palette.py        Primary HEX から Light/Dark を決定的に算出
    └── slugify.py               ブランド名から filesystem-safe な slug を生成
```

## 関連スキル

- [tekion-slide-generator-v4](../tekion-slide-generator-v4/) — 必須の生成エンジン本体
