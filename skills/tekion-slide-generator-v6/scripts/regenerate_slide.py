#!/usr/bin/env python3
"""
nano-banana Slide Generator v3 Flash - 単一スライド再生成スクリプト

特定の1枚だけを再生成し、バージョン管理付きで保存します。
既存ファイルは上書きせず、_v2, _v3... と連番で新バージョンを作成します。

v3 Flash: --image-size, --thinking-level, --grounding オプション追加

バージョニング規則:
    オリジナル:    ryoko-news-pipeline_01.png      （暗黙のv1）
    再生成1回目:  ryoko-news-pipeline_01_v2.png
    再生成2回目:  ryoko-news-pipeline_01_v3.png

環境変数:
    GEMINI_API_KEY: Gemini APIキー（--api-key未指定時に使用）
"""
import os
import sys
import argparse
import re
import subprocess
import base64
from pathlib import Path
from typing import List, Tuple


VERSION_PATTERN = re.compile(r'^(.+_\d+)(?:_v(\d+))?\.png$')


def extract_base_name(slide_arg: str) -> str:
    """--slide引数からベース名を抽出する"""
    name = slide_arg.strip()
    if name.endswith('.png'):
        name = name[:-4]
    version_suffix = re.search(r'_v\d+$', name)
    if version_suffix:
        name = name[:version_suffix.start()]
    return name


def scan_existing_versions(images_dir: Path, base_name: str) -> List[Tuple[Path, int]]:
    """指定ベース名の既存バージョンをスキャンする"""
    versions = []
    for png_file in images_dir.glob('*.png'):
        match = VERSION_PATTERN.match(png_file.name)
        if not match:
            continue
        file_base = match.group(1)
        if file_base != base_name:
            continue
        version_str = match.group(2)
        version = int(version_str) if version_str else 1
        versions.append((png_file, version))
    return sorted(versions, key=lambda x: x[1])


def determine_next_version(existing_versions: List[Tuple[Path, int]]) -> int:
    """次のバージョン番号を決定する"""
    if not existing_versions:
        return 1
    max_version = max(v for _, v in existing_versions)
    return max_version + 1


def build_output_filename(base_name: str, version: int) -> str:
    """出力ファイル名を生成する"""
    if version == 1:
        return f"{base_name}.png"
    return f"{base_name}_v{version}.png"


def regenerate_slide(
    slide_arg: str,
    session_dir: str,
    api_key: str,
    max_retries: int = 3,
    logo_path: str = None,
    image_size: str = "2K",
    thinking_level: str = "High",
    grounding: bool = False,
) -> bool:
    """単一スライドを再生成する"""
    session_path = Path(session_dir)
    images_dir = session_path / 'images'
    prompts_dir = session_path / 'prompts'

    base_name = extract_base_name(slide_arg)
    print(f"🔍 スライド識別: {base_name}", file=sys.stderr)

    prompt_file = prompts_dir / f"{base_name}.txt"
    if not prompt_file.exists():
        print(f"❌ エラー: プロンプトファイルが見つかりません: {prompt_file}", file=sys.stderr)
        print(f"   利用可能なプロンプト:", file=sys.stderr)
        if prompts_dir.exists():
            for f in sorted(prompts_dir.glob('*.txt')):
                print(f"     - {f.stem}", file=sys.stderr)
        return False

    prompt_text = prompt_file.read_text(encoding='utf-8')
    print(f"📝 プロンプト読み込み: {prompt_file.name} ({len(prompt_text):,} bytes)", file=sys.stderr)

    existing = scan_existing_versions(images_dir, base_name)
    if existing:
        print(f"📂 既存バージョン:", file=sys.stderr)
        for path, ver in existing:
            print(f"     v{ver}: {path.name}", file=sys.stderr)
    else:
        print(f"📂 既存バージョン: なし（初回生成）", file=sys.stderr)

    next_version = determine_next_version(existing)
    output_filename = build_output_filename(base_name, next_version)
    output_path = images_dir / output_filename

    print(f"🎯 生成先: {output_filename} (v{next_version})", file=sys.stderr)

    script_dir = Path(__file__).parent
    retry_script = script_dir / 'generate_slide_with_retry.py'

    if not retry_script.exists():
        print(f"❌ エラー: 生成スクリプトが見つかりません: {retry_script}", file=sys.stderr)
        return False

    cmd = [
        sys.executable, str(retry_script),
        '--prompt', prompt_text,
        '--output', str(output_path),
        '--api-key', api_key,
        '--max-retries', str(max_retries),
        '--image-size', image_size,
        '--thinking-level', thinking_level,
    ]
    if logo_path:
        cmd.extend(['--logo', logo_path])
    if grounding:
        cmd.append('--grounding')

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"🚀 Gemini API呼び出し開始 (v3 Flash)", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    try:
        result = subprocess.run(cmd, capture_output=False, text=True)

        if result.returncode == 0:
            print(f"\n{'='*60}", file=sys.stderr)
            print(f"✅ 再生成成功: {output_filename}", file=sys.stderr)
            if output_path.exists():
                file_size = output_path.stat().st_size
                print(f"   サイズ: {file_size:,} bytes", file=sys.stderr)
            print(f"   パス: {output_path}", file=sys.stderr)
            print(f"{'='*60}", file=sys.stderr)
            return True
        else:
            print(f"\n{'='*60}", file=sys.stderr)
            print(f"❌ 再生成失敗 (exit code: {result.returncode})", file=sys.stderr)
            print(f"{'='*60}", file=sys.stderr)
            return False

    except Exception as e:
        print(f"❌ 予期しないエラー: {type(e).__name__}: {e}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(
        description='単一スライドを再生成（バージョン管理付き・v3 Flash）',
    )
    parser.add_argument('--slide', required=True, help='再生成するスライド（ベース名、ファイル名、バージョン付き名のいずれか）')
    parser.add_argument('--session-dir', required=True, help='セッションディレクトリ（images/とprompts/を含む）')
    parser.add_argument('--api-key', help='Gemini APIキー（省略時は環境変数GEMINI_API_KEYを使用）')
    parser.add_argument('--max-retries', type=int, default=3, help='最大リトライ回数（デフォルト: 3）')
    parser.add_argument('--logo', help='ロゴ画像パス')
    parser.add_argument('--image-size', default='2K', choices=['512px', '1K', '2K', '4K'], help='出力解像度（デフォルト: 2K）')
    parser.add_argument('--thinking-level', default='High', choices=['minimal', 'High'], help='Thinkingレベル（デフォルト: High）')
    parser.add_argument('--grounding', action='store_true', help='Google画像検索グラウンディングを有効化')

    args = parser.parse_args()

    api_key = args.api_key or os.environ.get('GEMINI_API_KEY')
    if not api_key:
        print("❌ エラー: GEMINI_API_KEYが設定されていません", file=sys.stderr)
        sys.exit(1)

    success = regenerate_slide(
        slide_arg=args.slide,
        session_dir=args.session_dir,
        api_key=api_key,
        max_retries=args.max_retries,
        logo_path=args.logo,
        image_size=args.image_size,
        thinking_level=args.thinking_level,
        grounding=args.grounding,
    )
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
