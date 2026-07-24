#!/usr/bin/env python3
"""
nano-banana Slide Generator v3 Flash - Parallel Prompt Generation from JSON
Phase 2: Python Parallel Generation

このスクリプトは、Claude (Haiku)が生成したスライド分割計画JSONから
Jinja2テンプレートを使用してプロンプトを24並列で生成します。

v3 Flash:
  - slides_plan.jsonから_groundingフィールドを読み取りgrounding_map.jsonを生成
  - テンプレートにresolution変数を渡す

Usage:
    python generate_prompts_from_json.py \
        --session-dir slides_output \
        --json-file json/slides_plan.json \
        --output-dir prompts \
        --max-workers 24
"""

import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Any

try:
    from jinja2 import Template, Environment, FileSystemLoader
except ImportError:
    print("Error: Jinja2 is not installed. Please install it:")
    print("  pip install jinja2")
    sys.exit(1)


# 解像度名 → 想定ピクセル解像度のマッピング
# providers/codex.py の SIZE_MAP（最終出力解像度）と一致させること。
# モデルへの指示解像度と最終正規化先がずれると、指示と成果物が食い違う。
RESOLUTION_MAP = {
    "512px": "1280x720",
    "1K": "1792x1008",
    "2K": "2560x1440",
    "4K": "3840x2160",
}


def load_json_plan(json_path: str) -> List[Dict[str, Any]]:
    """
    スライド分割計画JSONを読み込む

    Args:
        json_path: JSONファイルのパス

    Returns:
        スライドデータのリスト
    """
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # スライドデータの検証（配列形式とオブジェクト形式の両方を受け入れ）
    if isinstance(data, list):
        slides = data
    elif isinstance(data, dict) and 'slides' in data:
        slides = data['slides']
    else:
        raise ValueError(f"Invalid JSON format: expected {{\"slides\": [...]}} or [...] in {json_path}")
    print(f"Loaded {len(slides)} slides from {json_path}")
    return slides


def load_shared_resources(session_dir: str, design_guidelines_path: str = None) -> Dict[str, str]:
    """
    共有リソース（デザインガイドライン、レイアウトパターン）を読み込む

    Args:
        session_dir: セッションディレクトリのパス（未使用、後方互換性のため保持）
        design_guidelines_path: デザインガイドラインファイルのパス（指定時はそちらを優先）

    Returns:
        共有リソースの辞書
    """
    resources = {}

    # デザインガイドラインの読み込み先を決定
    if design_guidelines_path:
        design_path = Path(design_guidelines_path)
    else:
        # デフォルト: スキルのreferences/ディレクトリ
        script_dir = Path(__file__).parent
        skill_dir = script_dir.parent
        design_path = skill_dir / "references" / "design_guidelines.md"

    if design_path.exists():
        with open(design_path, 'r', encoding='utf-8') as f:
            resources['design_guidelines'] = f.read()
        print(f"✓ Loaded design guidelines from {design_path}")
    else:
        print(f"Error: Design guidelines not found at {design_path}")
        sys.exit(1)

    return resources


def load_template(template_path: str) -> Template:
    """
    Jinja2テンプレートを読み込む

    Args:
        template_path: テンプレートファイルのパス

    Returns:
        Jinja2 Templateオブジェクト
    """
    template_dir = Path(template_path).parent
    template_name = Path(template_path).name

    # Jinja2環境を設定
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        trim_blocks=True,
        lstrip_blocks=True
    )

    template = env.get_template(template_name)
    return template


def build_chapter_contexts(slides: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    """
    各スライドのチャプターコンテキストを構築（方式C: 章アウトライン + ポジション）

    スライド間の一貫性を保つため、各スライドに以下の情報を付与:
    - chapter_title: 章タイトル（source_file由来）
    - slide_position: "N/M" 形式の位置情報
    - outline: 章内の全スライドタイトル一覧（現在位置マーカー付き）

    Args:
        slides: スライドデータのリスト

    Returns:
        スライドインデックス → chapter_context の辞書
    """
    chapters = {}
    for idx, slide in enumerate(slides):
        source = slide.get('source_file', '')
        if source == 'course_title':
            continue
        if source not in chapters:
            chapters[source] = []
        chapters[source].append((idx, slide))

    contexts = {}
    for source, chapter_slides in chapters.items():
        total = len(chapter_slides)
        if total <= 1:
            continue
        for pos, (idx, slide) in enumerate(chapter_slides):
            outline = []
            for i, (_, s) in enumerate(chapter_slides):
                title = s.get('title', '')
                if i == pos - 1:
                    outline.append(f"{i+1}. {title} ← 前のスライド")
                elif i == pos:
                    outline.append(f"{i+1}. {title} ← このスライド")
                elif i == pos + 1:
                    outline.append(f"{i+1}. {title} ← 次のスライド")
                else:
                    outline.append(f"{i+1}. {title}")
            contexts[idx] = {
                'chapter_title': source,
                'slide_position': f"{pos+1}/{total}",
                'outline': outline
            }

    return contexts


def extract_file_prefix(source_file: str) -> str:
    """
    ソースファイル名から接頭辞を抽出
    例: "4-1.1_デプロイの本質を理解する.md" → "4-1.1"
    例: "docs/研修内容/2026年1月版/第四回/4-2.2_Vercelデプロイ実践.md" → "4-2.2"

    Args:
        source_file: ソースファイル名(パスを含む場合もある)

    Returns:
        接頭辞文字列
    """
    if not source_file:
        return "slide"

    # パスの最後の部分(ファイル名のみ)を取得
    filename = source_file.split('/')[-1]

    # ファイル名から拡張子を削除
    basename = filename.replace('.md', '')

    # アンダースコアで分割して最初の部分を取得
    parts = basename.split('_')
    if parts:
        return parts[0]

    return "slide"


def extract_file_basename(source_file: str) -> str:
    """
    ソースファイル名から拡張子を除いた完全なベース名を抽出
    例: "4-1.1_デプロイの本質を理解する.md" → "4-1.1_デプロイの本質を理解する"
    例: "docs/研修内容/2026年1月版/第四回/4-2.2_Vercelデプロイ実践.md" → "4-2.2_Vercelデプロイ実践"

    Args:
        source_file: ソースファイル名(パスを含む場合もある)

    Returns:
        拡張子を除いたベース名
    """
    if not source_file:
        return "slide"

    # パスの最後の部分(ファイル名のみ)を取得
    filename = source_file.split('/')[-1]

    # ファイル名から拡張子を削除
    basename = filename.replace('.md', '')

    return basename


def extract_grounding_map(slides: List[Dict[str, Any]]) -> Dict[str, bool]:
    """
    slides_plan.jsonから_groundingフィールドを読み取り、grounding_mapを構築する。

    各スライドの_groundingフィールド（true/false）を読み取り、
    スライドのベース名をキーとしたマップを返す。

    Args:
        slides: スライドデータのリスト

    Returns:
        {slide_base_name: bool} のマッピング
    """
    grounding_map = {}
    for slide in slides:
        source_file = slide.get('source_file', '')
        file_basename = extract_file_basename(source_file)
        file_slide_number = slide.get('_file_slide_number', 1)
        slide_base = f"{file_basename}_{file_slide_number:02d}"
        grounding_map[slide_base] = slide.get('_grounding', False)
    return grounding_map


def save_grounding_map(session_dir: str, grounding_map: Dict[str, bool]) -> str:
    """
    grounding_map.jsonをセッションディレクトリに保存する。

    Args:
        session_dir: セッションディレクトリのパス
        grounding_map: {slide_base_name: bool} のマッピング

    Returns:
        保存先ファイルパス
    """
    output_path = Path(session_dir) / "grounding_map.json"
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(grounding_map, f, ensure_ascii=False, indent=2)

    grounding_on = sum(1 for v in grounding_map.values() if v)
    print(f"✓ Grounding map saved: {output_path}")
    print(f"  Grounding ON: {grounding_on}/{len(grounding_map)} slides")
    return str(output_path)


def generate_single_prompt(args: tuple) -> tuple:
    """
    単一スライドのプロンプトを生成

    Args:
        args: (slide_data, template_path, shared_resources, output_dir, per_file_counters, chapter_context, resolution)

    Returns:
        (slide_number, output_path, success)
    """
    slide_data, template_path, shared_resources, output_dir, per_file_counters, chapter_context, resolution = args

    try:
        slide_number = slide_data.get('slide_number', 0)
        source_file = slide_data.get('source_file', '')

        # 各プロセスでテンプレートを読み込み
        template = load_template(template_path)

        # Jinja2テンプレートでプロンプト生成
        prompt = template.render(
            slide=slide_data,
            design_guidelines=shared_resources['design_guidelines'],
            chapter_context=chapter_context,
            resolution=resolution
        )

        # ソースファイルからベース名を抽出
        file_basename = extract_file_basename(source_file)

        # ファイルごとの連番を取得
        file_slide_number = per_file_counters.get(source_file, 1)

        # ファイル名: 4-1.1_デプロイの本質を理解する_01.txt のような形式
        # 常に連番を付与（exportスクリプトのソート順を保証するため）
        output_filename = f"{file_basename}_{file_slide_number:02d}.txt"
        output_path = Path(output_dir) / output_filename

        # プロンプトをファイルに書き込み
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(prompt)

        # プロンプトサイズ確認（2.5KB以上が目標）
        prompt_size = len(prompt)
        if prompt_size < 2500:
            print(f"Warning: {output_filename} is only {prompt_size} bytes (target: 2500+)")

        return (slide_number, str(output_path), True)

    except Exception as e:
        print(f"Error generating prompt for slide {slide_data.get('slide_number', '?')}: {e}")
        return (slide_data.get('slide_number', 0), "", False)


def main():
    parser = argparse.ArgumentParser(
        description="Generate slide prompts from JSON plan using Jinja2 template (v3 Flash)"
    )
    parser.add_argument(
        '--session-dir',
        required=True,
        help='Session directory path (e.g., slides_output)'
    )
    parser.add_argument(
        '--json-file',
        default='json/slides_plan.json',
        help='JSON plan file path relative to session-dir (default: json/slides_plan.json)'
    )
    parser.add_argument(
        '--output-dir',
        default='prompts',
        help='Output directory relative to session-dir (default: prompts)'
    )
    parser.add_argument(
        '--max-workers',
        type=int,
        default=24,
        help='Maximum number of parallel workers (default: 24)'
    )
    parser.add_argument(
        '--template-path',
        help='Path to Jinja2 template (default: auto-detect by --style)'
    )
    parser.add_argument(
        '--style',
        default='balanced',
        choices=['balanced', 'visual'],
        help='スライドスタイル: balanced=営業資料・提案書風（デフォルト）/ visual=ピッチデッキ・Keynote風。各スライドのJSON内 _style でオーバーライド可能'
    )
    parser.add_argument(
        '--design-guidelines',
        help='Path to design guidelines markdown file (default: skill references/design_guidelines.md)'
    )
    parser.add_argument(
        '--image-size',
        default='2K',
        choices=['512px', '1K', '2K', '4K'],
        help='Output resolution for template rendering (default: 2K)'
    )

    args = parser.parse_args()

    # パス設定
    session_dir = Path(args.session_dir)
    json_path = session_dir / args.json_file
    output_dir = session_dir / args.output_dir

    # 出力ディレクトリ作成
    output_dir.mkdir(parents=True, exist_ok=True)

    # ダッシュボード実況: プロンプト生成ステージ
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).parent))
    from manifest_utils import set_session_status
    set_session_status(str(session_dir), "prompting", "画像プロンプトを生成中")

    # テンプレートパス設定（スタイル別 + --template-path オーバーライド）
    script_dir = Path(__file__).parent
    skill_dir = script_dir.parent
    templates_dir = skill_dir / "templates"

    STYLE_TEMPLATE_MAP = {
        'balanced': templates_dir / 'prompt_template_balanced.j2',
        'visual':   templates_dir / 'prompt_template_visual.j2',
    }

    if args.template_path:
        # 明示指定（全スライド共通で使用）
        default_template_path = Path(args.template_path)
        style_overrides_enabled = False
    else:
        default_template_path = STYLE_TEMPLATE_MAP.get(args.style, STYLE_TEMPLATE_MAP['balanced'])
        # 旧 prompt_template.j2 への後方互換フォールバック
        if not default_template_path.exists():
            legacy_path = templates_dir / 'prompt_template.j2'
            if legacy_path.exists():
                print(f"⚠️  Style template not found, falling back to legacy: {legacy_path}")
                default_template_path = legacy_path
        style_overrides_enabled = True

    if not default_template_path.exists():
        print(f"Error: Template not found at {default_template_path}")
        sys.exit(1)

    print(f"Style:           {args.style} (default)")
    print(f"Default template: {default_template_path.name}")
    if style_overrides_enabled:
        print(f"Per-slide _style override: enabled")

    # 解像度マッピング
    resolution = RESOLUTION_MAP.get(args.image_size, "2560x1440")
    print(f"Image size: {args.image_size} → resolution: {resolution}")

    # JSONプラン読み込み
    try:
        slides = load_json_plan(str(json_path))
    except FileNotFoundError:
        print(f"Error: JSON plan not found at {json_path}")
        sys.exit(1)
    except Exception as e:
        print(f"Error loading JSON plan: {e}")
        sys.exit(1)

    # 共有リソース読み込み（1回のみ）
    print("Loading shared resources...")
    shared_resources = load_shared_resources(str(session_dir), getattr(args, 'design_guidelines', None))

    # ファイルごとの連番カウンタを作成
    per_file_counters = {}
    for slide in slides:
        source_file = slide.get('source_file', '')
        if source_file not in per_file_counters:
            per_file_counters[source_file] = 0
        per_file_counters[source_file] += 1
        # 各スライドに現在のカウント値を保存
        slide['_file_slide_number'] = per_file_counters[source_file]

    # grounding_map.jsonを生成（v3 Flash: Phase 2のJSON内_groundingフィールドから抽出）
    grounding_map = extract_grounding_map(slides)
    save_grounding_map(str(session_dir), grounding_map)

    # チャプターコンテキストを構築（方式C: 章アウトライン + ポジション）
    chapter_contexts = build_chapter_contexts(slides)
    print(f"✓ Chapter contexts built for {len(chapter_contexts)} slides")

    # スライド毎のテンプレート解決（_style オーバーライドを反映）
    def resolve_template_for_slide(slide_data):
        if not style_overrides_enabled:
            return default_template_path
        slide_style = (slide_data.get('_style') or '').strip().lower()
        if slide_style in STYLE_TEMPLATE_MAP:
            return STYLE_TEMPLATE_MAP[slide_style]
        return default_template_path

    # スタイル別スライド数をカウント（ログ用）
    style_counts = {}
    for slide in slides:
        tpath = resolve_template_for_slide(slide)
        style_counts[tpath.name] = style_counts.get(tpath.name, 0) + 1
    for tname, cnt in style_counts.items():
        print(f"  {tname}: {cnt} slides")

    # 並列生成のための引数準備
    task_args = [
        (slide, str(resolve_template_for_slide(slide)), shared_resources, str(output_dir),
         {slide.get('source_file', ''): slide.get('_file_slide_number', 1)},
         chapter_contexts.get(idx),
         resolution)
        for idx, slide in enumerate(slides)
    ]

    success_count = 0
    failed_count = 0

    # Jinja レンダリングは軽いため、少枚数ではプロセス起動コストの方が高い。
    # 32枚以下は単一プロセスで即時に書き切る
    if len(slides) <= 32 or args.max_workers <= 1:
        print(f"Generating {len(slides)} prompts (single process)...")
        for task_arg in task_args:
            _, _, success = generate_single_prompt(task_arg)
            success_count += 1 if success else 0
            failed_count += 0 if success else 1
    else:
        print(f"Generating {len(slides)} prompts with {args.max_workers} parallel workers...")
        with ProcessPoolExecutor(max_workers=args.max_workers) as executor:
            futures = {executor.submit(generate_single_prompt, task_arg): task_arg[0]
                       for task_arg in task_args}
            for future in as_completed(futures):
                slide_number, output_path, success = future.result()
                if success:
                    success_count += 1
                else:
                    failed_count += 1

    # 結果サマリー
    print("\n" + "="*60)
    print("Prompt Generation Summary (v3 Flash)")
    print("="*60)
    print(f"Total slides:        {len(slides)}")
    print(f"Successfully generated: {success_count}")
    print(f"Failed:              {failed_count}")
    print(f"Output directory:    {output_dir}")
    print(f"Grounding map:       {session_dir}/grounding_map.json")
    print("="*60)

    if failed_count > 0:
        sys.exit(1)

    # 計画から外れた古いプロンプトを掃除する（残すと Phase 7 が旧スライドまで生成し、
    # export にも混入する）。今回の slides_plan にあるファイル名だけを正とする
    planned_bases = {
        f"{extract_file_basename(slide.get('source_file', ''))}_{slide.get('_file_slide_number', 1):02d}"
        for slide in slides
    }
    removed_stale = []
    for txt in Path(output_dir).glob("*.txt"):
        if txt.stem not in planned_bases:
            try:
                txt.unlink()
                removed_stale.append(txt.stem)
            except OSError:
                pass
    if removed_stale:
        print(f"🧹 計画外の古いプロンプトを削除: {', '.join(sorted(removed_stale))}")

    # ダッシュボード実況: 全スライドをプレースホルダー登録（生成前から枠が見える）。
    # 保存は locked_update（生成・編集・ハブ操作との lost update 防止）
    try:
        from manifest_utils import locked_update, get_entry, update_entry
        manifest_path = session_dir / "manifest.json"

        def _register_planned(m):
            for base in planned_bases:
                entry = get_entry(m, base)
                if entry.get("state") != "validated":
                    update_entry(m, base, state="planned")

        locked_update(str(manifest_path), _register_planned)
        set_session_status(str(session_dir), "prompted",
                           f"プロンプト生成完了。画像生成の開始を待機中", total=len(slides))
    except Exception as e:
        print(f"⚠️  placeholder registration skipped: {e}")

    print("\n✓ All prompts generated successfully!")


if __name__ == '__main__':
    main()
