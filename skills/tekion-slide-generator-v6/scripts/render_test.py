#!/usr/bin/env python3
"""
nano-banana Slide Generator v2 - Render Test (API不要)

テンプレートの出力を検証するスクリプト。Gemini APIを呼び出さずに
プロンプトの内容・構造・禁止項目を確認できる。

Usage:
    # slides_plan.jsonから全プロンプトをレンダリングして検証
    python render_test.py --session-dir slides_output --design-guidelines slides_output/design_guidelines.md

    # 特定スライドのみ表示
    python render_test.py --session-dir slides_output --design-guidelines slides_output/design_guidelines.md --slide 3

    # 検証のみ（プロンプト本文を表示しない）
    python render_test.py --session-dir slides_output --design-guidelines slides_output/design_guidelines.md --validate-only
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Any, Tuple

try:
    from jinja2 import Environment, FileSystemLoader
except ImportError:
    print("Error: Jinja2 is not installed. pip install jinja2")
    sys.exit(1)


# プロンプトに必須のセクション
REQUIRED_SECTIONS = [
    'constraints:',
    '[Content Data]',
    '[Design Guidelines]',
    'output_spec:',
    'forbidden:',
    'logo:',
]


def load_slides_plan(session_dir: Path) -> List[Dict[str, Any]]:
    json_path = session_dir / 'json' / 'slides_plan.json'
    if not json_path.exists():
        print(f"Error: {json_path} not found")
        sys.exit(1)
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    return data.get('slides', [])


def load_template_and_resources(design_guidelines_path: str = None) -> Tuple:
    script_dir = Path(__file__).parent
    skill_dir = script_dir.parent
    template_dir = skill_dir / 'templates'
    template_path = template_dir / 'prompt_template.j2'

    if not template_path.exists():
        print(f"Error: Template not found at {template_path}")
        sys.exit(1)

    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template('prompt_template.j2')

    # デザインガイドライン読み込み
    design_guidelines = ''
    if design_guidelines_path:
        design_path = Path(design_guidelines_path)
    else:
        design_path = skill_dir / 'references' / 'design_guidelines_template.md'

    if design_path.exists():
        with open(design_path, 'r', encoding='utf-8') as f:
            design_guidelines = f.read()
        print(f"Loaded design guidelines from {design_path}")
    else:
        print(f"Warning: Design guidelines not found at {design_path}")

    return template, design_guidelines


def build_chapter_contexts(slides: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    """generate_prompts_from_json.pyと同じロジック"""
    chapters: Dict[str, list] = {}
    for idx, slide in enumerate(slides):
        source = slide.get('source_file', '')
        if source == 'course_title':
            continue
        if source not in chapters:
            chapters[source] = []
        chapters[source].append((idx, slide))

    contexts: Dict[int, Dict[str, Any]] = {}
    for source, chapter_slides in chapters.items():
        total = len(chapter_slides)
        if total <= 1:
            continue
        for pos, (idx, slide) in enumerate(chapter_slides):
            outline = []
            for i, (_, s) in enumerate(chapter_slides):
                title = s.get('title', '')
                if i == pos - 1:
                    outline.append(f"{i+1}. {title} <- \u524d\u306e\u30b9\u30e9\u30a4\u30c9")
                elif i == pos:
                    outline.append(f"{i+1}. {title} <- \u3053\u306e\u30b9\u30e9\u30a4\u30c9")
                elif i == pos + 1:
                    outline.append(f"{i+1}. {title} <- \u6b21\u306e\u30b9\u30e9\u30a4\u30c9")
                else:
                    outline.append(f"{i+1}. {title}")
            contexts[idx] = {
                'chapter_title': source,
                'slide_position': f"{pos+1}/{total}",
                'outline': outline,
            }
    return contexts


def validate_prompt(prompt: str, slide: Dict[str, Any]) -> List[str]:
    """プロンプトの品質チェック。問題があればメッセージのリストを返す"""
    issues: List[str] = []

    # 必須セクションの存在チェック
    for section in REQUIRED_SECTIONS:
        if section not in prompt:
            issues.append(f"MISSING: '{section}' section not found")

    # プロンプトサイズ
    size = len(prompt)
    if size < 2000:
        issues.append(f"SIZE: {size} bytes (target: 2000+)")

    # スライド種別とテンプレート分岐の整合性チェック（contentマーカーベース）
    content = slide.get('content', '')
    if '\u8b1b\u5ea7\u30bf\u30a4\u30c8\u30eb\u30b9\u30e9\u30a4\u30c9\uff08\u8868\u7d19\uff09' in content:
        if 'Pattern M' not in prompt:
            issues.append("MISMATCH: cover slide but Pattern M not in prompt")
    elif '\u4e2d\u6249\u30b9\u30e9\u30a4\u30c9\uff1a\u30bf\u30a4\u30c8\u30eb\u3068\u30b5\u30d6\u30bf\u30a4\u30c8\u30eb\u306e\u307f\u8868\u793a' in content:
        if 'Pattern L' not in prompt:
            issues.append("MISMATCH: divider slide but Pattern L not in prompt")
    else:
        if 'layout_selection' not in prompt:
            issues.append("MISMATCH: content slide but layout_selection not in prompt")

    # コンテンツが空でないか
    if not content.strip():
        issues.append("EMPTY: slide content is empty")

    # タイトルが空でないか
    if not slide.get('title', '').strip():
        issues.append("EMPTY: slide title is empty")

    return issues


def render_slide(template, design_guidelines: str, slide: Dict[str, Any],
                 chapter_context: Dict[str, Any] = None) -> str:
    return template.render(
        slide=slide,
        design_guidelines=design_guidelines,
        chapter_context=chapter_context,
    )


def main():
    parser = argparse.ArgumentParser(
        description='Render and validate slide prompts without calling Gemini API'
    )
    parser.add_argument(
        '--session-dir',
        required=True,
        help='Session directory (e.g., slides_output)',
    )
    parser.add_argument(
        '--design-guidelines',
        help='Path to design guidelines file (default: skill references/design_guidelines_template.md)',
    )
    parser.add_argument(
        '--slide',
        type=int,
        default=None,
        help='Show only this slide number',
    )
    parser.add_argument(
        '--validate-only',
        action='store_true',
        help='Run validation only (do not print prompt text)',
    )
    args = parser.parse_args()

    session_dir = Path(args.session_dir)
    slides = load_slides_plan(session_dir)
    template, design_guidelines = load_template_and_resources(args.design_guidelines)
    chapter_contexts = build_chapter_contexts(slides)

    print("=" * 70)
    print("nano-banana Render Test v2 (API-free)")
    print("=" * 70)
    print(f"Slides: {len(slides)}")
    print(f"Chapters with context: {len(set(c['chapter_title'] for c in chapter_contexts.values()))}")
    print("=" * 70)

    total_issues = 0
    issue_slides: List[int] = []

    for idx, slide in enumerate(slides):
        slide_num = slide.get('slide_number', idx)

        if args.slide is not None and slide_num != args.slide:
            continue

        chapter_ctx = chapter_contexts.get(idx)
        prompt = render_slide(template, design_guidelines, slide, chapter_ctx)
        issues = validate_prompt(prompt, slide)

        content = slide.get('content', '')
        if '\u8b1b\u5ea7\u30bf\u30a4\u30c8\u30eb\u30b9\u30e9\u30a4\u30c9\uff08\u8868\u7d19\uff09' in content:
            slide_kind = 'cover'
        elif '\u4e2d\u6249\u30b9\u30e9\u30a4\u30c9\uff1a\u30bf\u30a4\u30c8\u30eb\u3068\u30b5\u30d6\u30bf\u30a4\u30c8\u30eb\u306e\u307f\u8868\u793a' in content:
            slide_kind = 'divider'
        else:
            slide_kind = 'content'
        has_ctx = 'ctx' if chapter_ctx else '---'
        has_key = 'key' if slide.get('key_message') else '---'
        status = 'PASS' if not issues else 'FAIL'

        print(f"\n{'='*70}")
        print(f"[{status}] Slide #{slide_num} | {slide_kind} | {has_ctx} | {has_key}")
        print(f"  title: {slide.get('title', '')}")
        print(f"  source: {slide.get('source_file', '')}")
        print(f"  prompt size: {len(prompt)} bytes")

        if issues:
            total_issues += len(issues)
            issue_slides.append(slide_num)
            for issue in issues:
                print(f"  ! {issue}")

        if not args.validate_only:
            print(f"\n--- Rendered Prompt ({len(prompt)} chars) ---")
            print(prompt[:3000])
            if len(prompt) > 3000:
                print(f"\n... ({len(prompt) - 3000} chars truncated)")
            print("--- End ---")

    # サマリー
    print(f"\n{'='*70}")
    print("Render Test Summary")
    print("=" * 70)
    print(f"Total slides:    {len(slides)}")
    print(f"Total issues:    {total_issues}")
    if issue_slides:
        print(f"Problem slides:  {issue_slides}")
    else:
        print("All slides passed validation.")
    print("=" * 70)

    sys.exit(1 if total_issues > 0 else 0)


if __name__ == '__main__':
    main()
