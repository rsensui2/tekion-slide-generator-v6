#!/usr/bin/env python3
"""常駐ハブのランタイム版を1つの文字列にする。

ハブは scripts/ を ~/.tekion-slides/hub/ へ**コピー**して常駐する。だから
「今動いているハブは、今から使おうとしているスキルと同じコードか？」を
判定できないと、直したはずの不具合が直っていない状態で動き続ける。

版番号だけでは足りない:
  - プラグイン経由でない導入（~/.claude/skills へ直接置く等）には版番号が無く、
    固定値を名乗ると永遠に「最新」に見える（実際にそうなっていた）
  - 版を上げ忘れたまま中身だけ変わることもある

そこで **プラグインの版 + 実体のハッシュ** を版とする。中身が1バイトでも
違えば違う文字列になるので、導入方法にかかわらず食い違いを検知できる。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

# ランタイムとして配置され、挙動を左右するファイル
TRACKED_SUFFIXES = (".py",)


def plugin_version(start: Path) -> str | None:
    """親をたどってプラグインの版を探す（無ければ None）。"""
    for parent in start.resolve().parents:
        manifest = parent / ".codex-plugin" / "plugin.json"
        try:
            with manifest.open("r", encoding="utf-8") as handle:
                version = json.load(handle).get("version")
            if version:
                return str(version)
        except (OSError, ValueError, AttributeError):
            continue
    return None


def digest(scripts_dir: Path) -> str:
    """scripts/ 配下の実体から短いハッシュを作る（並び順は固定）。"""
    sha = hashlib.sha256()
    files = []
    for root, dirnames, filenames in os.walk(scripts_dir):
        dirnames[:] = sorted(d for d in dirnames
                             if d not in ("__pycache__", "tests", ".git"))
        for name in sorted(filenames):
            if name.endswith(TRACKED_SUFFIXES):
                files.append(Path(root) / name)
    for path in sorted(files):
        sha.update(str(path.relative_to(scripts_dir)).encode("utf-8"))
        try:
            sha.update(path.read_bytes())
        except OSError:
            sha.update(b"<unreadable>")
    return sha.hexdigest()[:8]


def fingerprint(scripts_dir: str | os.PathLike | None = None) -> str:
    path = Path(scripts_dir or os.path.dirname(os.path.abspath(__file__)))
    return f"{plugin_version(path) or 'src'}+{digest(path)}"


if __name__ == "__main__":
    print(fingerprint(sys.argv[1] if len(sys.argv) > 1 else None))
