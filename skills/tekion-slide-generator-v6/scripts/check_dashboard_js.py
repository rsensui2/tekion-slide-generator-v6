#!/usr/bin/env python3
"""ダッシュボード（review_deck.py）のJSを node --check で構文検証する。

PAGE_TEMPLATE は Python 文字列なので、JS 内に '\\n' のつもりで '\n' と書くと
生の改行が入り、文字列リテラルが壊れてスクリプト全体が沈黙する（全ボタン無反応）。
review_deck.py の <script> を編集したら必ずこれを実行すること:

    python3 check_dashboard_js.py
"""
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import review_deck  # noqa: E402


def main() -> int:
    blocks = re.findall(r"<script>(.*?)</script>", review_deck.PAGE_TEMPLATE, re.S)
    if not blocks:
        print("❌ PAGE_TEMPLATE に <script> が見つかりません")
        return 1
    for i, js in enumerate(blocks):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(js)
            path = f.name
        try:
            r = subprocess.run(["node", "--check", path], capture_output=True, text=True)
        except FileNotFoundError:
            print("⚠️  node が無いため構文検証をスキップしました")
            return 0
        finally:
            os.unlink(path)
        if r.returncode != 0:
            print(f"❌ ダッシュボードJSの構文エラー (block {i + 1}):\n{r.stderr}")
            return 1
    print(f"✅ ダッシュボードJS構文 OK（{len(blocks)} block）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
