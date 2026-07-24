#!/bin/bash
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo ""
echo "============================================"
echo "  TEKION Slide Generator v5 — Codex版"
echo "  セットアップ"
echo "============================================"
echo ""

# --------------------------------------------------------
# 1. Python チェック
# --------------------------------------------------------
if ! command -v python3 &> /dev/null; then
    echo "❌ python3 が見つかりません。"
    echo ""
    echo "   Python 3.10 以上をインストールしてください:"
    echo "     Mac:   brew install python3"
    echo "     Ubuntu: sudo apt install python3 python3-pip"
    echo "     Windows: https://www.python.org/downloads/"
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "✓ Python ${PYTHON_VERSION} を検出しました"

# --------------------------------------------------------
# 2. 依存パッケージの自動インストール
# --------------------------------------------------------
MISSING=()
python3 -c "import PIL" 2>/dev/null || MISSING+=("Pillow>=10.0.0")
python3 -c "import pptx" 2>/dev/null || MISSING+=("python-pptx>=0.6.21")
python3 -c "import requests" 2>/dev/null || MISSING+=("requests>=2.31.0")
python3 -c "import jinja2" 2>/dev/null || MISSING+=("Jinja2>=3.1.0")
python3 -c "import fitz" 2>/dev/null || MISSING+=("pymupdf>=1.24.0")  # PDF取り込み用

if [ ${#MISSING[@]} -gt 0 ]; then
    echo ""
    echo "📦 不足パッケージをインストールします: ${MISSING[*]}"
    pip3 install "${MISSING[@]}" -q 2>/dev/null || python3 -m pip install "${MISSING[@]}" -q
    echo "✓ インストール完了"
else
    echo "✓ 必要なパッケージはすべてインストール済みです"
fi

# 最終確認
if ! python3 -c "import PIL, pptx, requests, jinja2" 2>/dev/null; then
    echo ""
    echo "❌ パッケージのインストールに失敗しました。"
    echo "   手動でインストールしてください:"
    echo ""
    echo "   pip3 install Pillow python-pptx requests Jinja2"
    echo ""
    exit 1
fi

echo "✓ 全パッケージの動作確認 OK"

# --------------------------------------------------------
# 3. Codex CLI とログインの確認（画像生成はサブスク枠で行う）
# --------------------------------------------------------
echo ""
if ! command -v codex &> /dev/null; then
    echo "❌ codex CLI が見つかりません。"
    echo "   Codex CLI をインストールしてください: https://developers.openai.com/codex"
    exit 1
fi
CODEX_VERSION=$(codex --version 2>/dev/null || echo "unknown")
echo "✓ Codex CLI を検出しました (${CODEX_VERSION})"

CODEX_HOME_DIR="${CODEX_HOME:-${HOME}/.codex}"
if [ -f "${CODEX_HOME_DIR}/auth.json" ]; then
    echo "✓ Codex ログイン認証を確認しました (${CODEX_HOME_DIR}/auth.json)"
else
    echo "⚠️  Codex に未ログインです。一度 'codex' を起動して ChatGPT/Codex でログインしてください。"
    echo "    画像生成はサブスクリプション枠で行われます（OpenAI APIキーは不要）。"
fi

# app-server バックエンドの存在確認（experimental・任意）
if codex app-server --help >/dev/null 2>&1; then
    echo "✓ codex app-server 利用可能（CODEX_SLIDES_BACKEND=app-server で選択可）"
fi

# --------------------------------------------------------
# 完了
# --------------------------------------------------------
echo ""
echo "============================================"
echo "  ✓ セットアップ完了！"
echo "============================================"
echo ""
echo "  この版は OpenAI APIキー不要。Codex サブスク枠で画像生成します。"
echo "  サブスク枠維持のため OPENAI_API_KEY は unset 推奨（ブリッジも自動除去）。"
echo ""
echo "  使い方:"
echo "    Claude Code で「Codexでスライドを作って」と話しかけると起動します。"
echo ""
