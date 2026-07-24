#!/usr/bin/env python3
"""アクティブプリセットのブランド設定を解決して shell 変数として出力する。

プリセットごとの機械可読設定は references/presets/<slug>.config.json に置く（任意）:

    {
      "logo": "assets/<slug>/logo.png",     // presets ディレクトリからの相対 or 絶対パス
      "logo_position": "bottom-right",       // bottom-right / bottom-left / top-right / top-left
      "logo_scale": 0.09,                    // 画像幅に対するロゴ幅比率
      "footer_text": "©2026 Example Inc."   // 省略時はデフォルト透かし / "" でフッター無し
    }

config が無い場合は従来デフォルト（グローバル assets/logo.png・右下・0.09）に解決する。

使い方（SKILL.md Phase 7 の直前で）:
    eval "$(python3 "${SKILL_DIR}/scripts/resolve_brand.py")"
    # → LOGO / SLIDE_LOGO_POSITION / SLIDE_LOGO_SCALE（/ SLIDE_FOOTER_TEXT）が設定される
"""
import argparse
import json
import shlex
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent


def _resolve_presets_dir() -> Path:
    """プリセット置き場: 環境変数 → ユーザーホーム → スキル内（後方互換）の順。

    プラグイン更新でスキルフォルダは丸ごと入れ替わるため、ユーザーのブランド設定は
    ~/.tekion-slides/presets に置くのが正。スキル内は同梱テンプレートの置き場。
    """
    import os as _os
    env = _os.environ.get("TEKION_PRESETS_DIR")
    if env:
        return Path(env).expanduser()
    user_dir = Path.home() / ".tekion-slides" / "presets"
    if user_dir.is_dir():
        return user_dir
    return SKILL_DIR / "references" / "presets"


PRESETS_DIR = _resolve_presets_dir()
DEFAULT_LOGO = SKILL_DIR / "assets" / "logo.png"
VALID_POSITIONS = ("bottom-right", "bottom-left", "top-right", "top-left")


def resolve() -> dict:
    active_file = PRESETS_DIR / ".active_preset"
    slug = None
    if active_file.is_file():
        name = active_file.read_text(encoding="utf-8").strip()
        if name.endswith(".md"):
            name = name[:-3]
        slug = name or None

    result = {
        "preset": f"{slug}.md" if slug else "example-preset.md",
        "logo": str(DEFAULT_LOGO),
        "logo_position": "bottom-right",
        "logo_scale": 0.09,
        "footer_text": None,  # None = デフォルト透かしのまま
    }

    if slug:
        preset_logo = PRESETS_DIR / "assets" / slug / "logo.png"
        if preset_logo.is_file():
            result["logo"] = str(preset_logo)

        config_file = PRESETS_DIR / f"{slug}.config.json"
        if config_file.is_file():
            try:
                config = json.loads(config_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                print(f"# WARNING: {config_file.name} のパースに失敗: {e}", file=sys.stderr)
                config = {}
            logo = config.get("logo")
            if logo:
                logo_path = Path(logo) if Path(logo).is_absolute() else PRESETS_DIR / logo
                if logo_path.is_file():
                    result["logo"] = str(logo_path)
                else:
                    print(f"# WARNING: config のロゴが見つかりません: {logo_path}", file=sys.stderr)
            position = config.get("logo_position")
            if position in VALID_POSITIONS:
                result["logo_position"] = position
            elif position is not None:
                print(f"# WARNING: 不正な logo_position: {position}", file=sys.stderr)
            scale = config.get("logo_scale")
            if isinstance(scale, (int, float)) and 0.02 <= scale <= 0.30:
                result["logo_scale"] = float(scale)
            elif scale is not None:
                print(f"# WARNING: logo_scale は 0.02〜0.30 で指定: {scale}", file=sys.stderr)
            if "footer_text" in config:
                result["footer_text"] = config["footer_text"]

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="JSON で出力（確認用）")
    args = parser.parse_args()

    result = resolve()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    print(f"LOGO={shlex.quote(result['logo'])}")
    print(f"export SLIDE_LOGO_POSITION={shlex.quote(result['logo_position'])}")
    print(f"export SLIDE_LOGO_SCALE={shlex.quote(str(result['logo_scale']))}")
    if result["footer_text"] is not None:
        print(f"export SLIDE_FOOTER_TEXT={shlex.quote(result['footer_text'])}")


if __name__ == "__main__":
    main()
