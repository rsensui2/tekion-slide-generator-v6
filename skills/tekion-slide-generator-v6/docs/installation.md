# 📦 Installation Guide

slide-generator の完全インストール手順。所要時間 5-10分。

---

## 前提条件

| 要件 | バージョン | 備考 |
|------|-----------|------|
| macOS / Linux | — | Windows は WSL2 推奨 |
| Python | 3.10+ | `python3 --version` で確認 |
| pip | 最新 | `pip install -U pip` |
| Claude Code | v2.x 以降 | [公式](https://claude.com/claude-code) |
| Git | 任意バージョン | clone 用 |
| Codex CLI | インストール済み・ログイン済み | ChatGPT/Codex サブスクで認証（画像生成に使用） |

---

## 1. リポジトリ取得

```bash
git clone https://github.com/rsensui2/tekion-slide-generator-v5.git
cd tekion-slide-generator-v5
```

このリポジトリには Claude Code 版・Codex ネイティブ版の 2 種類が同梱されている。
以下は **Claude Code 版**（`skills/claude-code/tekion-slide-generator-v5`）の手順。

---

## 2. Claude Code Skill としてインストール

置くのは **リポジトリ全体ではなく、Claude Code 版のスキルフォルダ1個**（`skills/claude-code/tekion-slide-generator-v5`）。

### オプション A: ディレクトリ配置（推奨・開発しやすい）

```bash
# スキル1個ぶんのフォルダだけをコピー
cp -R skills/claude-code/tekion-slide-generator-v5 ~/.claude/skills/

# 再起動後、Claude Code で自動認識
```

### オプション B: `.skill` パッケージ（配布用）

将来 GitHub Release で `.skill` ファイルを配布予定。現状は自前ビルド:

```bash
python3 /path/to/skill-creator/scripts/package_skill.py skills/claude-code/tekion-slide-generator-v5 ~/.claude/skills/
```

→ `~/.claude/skills/tekion-slide-generator-v5.skill` が生成される。

### オプション C: シンボリックリンク（更新が常に反映される）

```bash
ln -s "$(pwd)/skills/claude-code/tekion-slide-generator-v5" ~/.claude/skills/tekion-slide-generator-v5
```

git pull するだけで最新版が反映される。開発者向け。

---

## 3. Python 依存のインストール

```bash
pip install -r ~/.claude/skills/tekion-slide-generator-v5/requirements.txt
```

内訳:
- `Pillow>=10.0.0` — 画像処理・PDF生成
- `python-pptx>=0.6.21` — PPTX 出力
- `requests>=2.31.0` — HTTP通信
- `Jinja2>=3.1.0` — プロンプトテンプレート

### 仮想環境（推奨）

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## 4. セットアップスクリプトの実行

このスキル（Codex 駆動版）は **OpenAI API キーは不要**。画像生成は Codex 内蔵
gpt-image-2 を ChatGPT/Codex のサブスク枠で利用する。前提の確認はスクリプトに任せる:

```bash
bash ~/.claude/skills/tekion-slide-generator-v5/scripts/setup.sh
```

Python・依存4種・`codex` コマンド・`~/.codex/auth.json`（ログイン状態）を順にチェックし、
末尾に「この版は OpenAI APIキー不要。Codex サブスク枠で画像生成します」と表示されれば成功。

未ログインと警告が出た場合は、`codex` を一度起動して ChatGPT/Codex アカウントでログインする:

```bash
codex login
codex login status   # ログイン確認
codex exec "hello"    # 実行確認（テキスト応答が返れば疎通OK）
```

### API 課金版（OpenAI / Gemini）を使いたい場合のみ

既定の Codex 版を使わず、従量課金の OpenAI / Gemini API で生成したい場合のみ以下を設定する
（通常は不要）:

```bash
echo 'OPENAI_API_KEY=sk-proj-xxxxxxxxxxxxxxxxxxxxxxxx' >> ~/.claude/.env.local
echo 'GEMINI_API_KEY=AIzaSyXXXXXXXXXXXXXXXXXXXXXXXXXXX' >> ~/.claude/.env.local
chmod 600 ~/.claude/.env.local
```

利用時は `--provider openai` / `--provider gemini` を明示する。

---

## 5. Claude Code 起動

スキルは並列 Bash / ファイル操作を伴うため、許可ダイアログが多く出ることがある。
頻出する読み取り系コマンドは `~/.claude/settings.json` の `permissions.allow` に追加して
許可プロンプトを減らすのが安全な運用（`/fewer-permission-prompts` も活用可）。

> セキュリティ上、`--dangerously-skip-permissions` の常用は推奨しない。
> 信頼できる環境で一時的に使う場合のみ自己判断で。

### 初回起動時の確認

Claude Code を起動して以下を確認:

```
/skills
```

`slide-generator` が表示されれば成功。

---

## 6. 動作テスト

### 簡単なスライド1枚生成（Codex経由）

```bash
# 作業ディレクトリ
TEST=~/Desktop/slide-test
mkdir -p ${TEST}/{prompts,images}

# プロンプト作成
cat > ${TEST}/prompts/test_01.txt <<'EOF'
A clean presentation slide with title "テスト成功"
in the center, minimal design, white background with blue accent.
※スライド上の全テキストは日本語で表示すること。
EOF

# 画像生成（Codex サブスク枠。APIキー不要）
python3 ~/.claude/skills/tekion-slide-generator-v5/scripts/generate_slide_with_retry.py \
  --provider codex \
  --prompt "$(cat ${TEST}/prompts/test_01.txt)" \
  --output ${TEST}/images/test_01.png \
  --image-size 1K
```

`${TEST}/images/test_01.png` が生成されれば完了（1枚あたり数十秒〜1分程度かかる）。
最も確実なのは、実際に Claude Code へ「この内容を1枚のスライドにして」と話しかけて
16:9 画像が出るところまで確認する方法。

---

## トラブルシューティング

### `ModuleNotFoundError: No module named 'PIL'`

```bash
pip install Pillow python-pptx requests Jinja2
```

### OpenAI 500 エラー

gpt-image-2 は新モデルでサーバー側の一時障害が起きることがある。
- 数分待つ
- `--provider gemini` でフォールバック
- 別モデルで切り分け: `--model gpt-image-1`（未実装。希望があればPR）

### Gemini 429（レート制限）

- 無料枠の上限に達している
- 並列数を下げる: `--max-parallel 5`

### 日本語が化ける

- 生成された PNG は UTF-8 ベース、問題なし
- PPTX/PDF で化ける場合は Adobe Reader / PowerPoint 最新版推奨

---

## アンインストール

```bash
rm -rf ~/.claude/skills/tekion-slide-generator-v5
rm -f ~/.claude/skills/tekion-slide-generator-v5.skill
# APIキーは残しておいて問題なし（他のスキル/ツールで使う場合）
```
