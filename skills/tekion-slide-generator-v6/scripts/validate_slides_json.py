#!/usr/bin/env python3
"""
nano-banana Slide Generator - JSON Validation Script

Phase 1の出力（chunk_*.json / slides_plan.json）を検証し、
Phase 2に渡す前に不正データを検出する。

Usage:
    # slides_plan.jsonを検証
    python validate_slides_json.py --file slides_output/json/slides_plan.json

    # chunk単体を検証
    python validate_slides_json.py --file slides_output/json/chunk_0.json

    # ディレクトリ内の全JSONを検証
    python validate_slides_json.py --dir slides_output/json
"""

import argparse
import glob
import json
import os
import sys
from typing import Dict, List, Any, Tuple


# 許可されたフィールド
# v6: content の3分割フィールドを追加。表示文字（display_copy）とビジュアル指示
# （visual_brief）を分離すると、指示文がスライド上に誤描画される事故が減る。
ALLOWED_SLIDE_FIELDS = {
    'slide_number',  # int (required)
    'source_file',   # str (required)
    'title',         # str (required)
    'subtitle',      # str (required)
    'content',       # str (required ※display_copy 使用時は省略可)
    'key_message',   # str (optional)
    'display_copy',  # str (v6 optional): スライドに描画する文字そのもの（全行描画される）
    'visual_brief',  # str (v6 optional): 背景写真・図解・配色などのビジュアル指示（描画されない）
    'layout_hint',   # str (v6 optional): レイアウトヒント（例: "Pattern C: 左右スプリット"）
}

REQUIRED_SLIDE_FIELDS = {
    'slide_number',
    'source_file',
    'title',
    'subtitle',
}

ALLOWED_CHUNK_FIELDS = {
    'chunk_id',
    'processed_files',
    'slides',
    'total_slides',
    'completed_at',
    'error',  # エラー時のみ
}

# 禁止フィールド（SubAgent定義の「禁止されているフィールド」）
FORBIDDEN_SLIDE_FIELDS = {
    'slide_type',
    'visual_description',
    'layout',
    'design_notes',
    'image_prompt',
    'background_color',
}


def validate_slide(slide: Dict[str, Any], idx: int) -> List[str]:
    """単一スライドを検証"""
    issues = []

    # 必須フィールドのチェック
    for field in REQUIRED_SLIDE_FIELDS:
        if field not in slide:
            issues.append(f"slide[{idx}]: missing required field '{field}'")

    # v6: content か display_copy のどちらかは必須（両方無いと画像に描く内容が無い）
    if 'content' not in slide and 'display_copy' not in slide:
        issues.append(f"slide[{idx}]: either 'content' or 'display_copy' is required")
    # display_copy を使う場合は visual_brief も推奨（無くてもエラーにはしない）

    # 型チェック
    if 'slide_number' in slide and not isinstance(slide['slide_number'], int):
        issues.append(
            f"slide[{idx}]: slide_number must be int, got {type(slide['slide_number']).__name__}"
        )

    for str_field in ('source_file', 'title', 'subtitle', 'content',
                      'display_copy', 'visual_brief', 'layout_hint'):
        if str_field in slide and not isinstance(slide[str_field], str):
            issues.append(
                f"slide[{idx}]: {str_field} must be str, got {type(slide[str_field]).__name__}"
            )

    if 'key_message' in slide and not isinstance(slide['key_message'], str):
        issues.append(
            f"slide[{idx}]: key_message must be str, got {type(slide['key_message']).__name__}"
        )

    # 禁止フィールドのチェック
    for field in FORBIDDEN_SLIDE_FIELDS:
        if field in slide:
            issues.append(f"slide[{idx}]: forbidden field '{field}' found")

    # 未知フィールドのチェック（_プレフィックスは内部用として許可: _grounding, _file_slide_number等）
    for field in slide:
        if field.startswith('_'):
            continue
        if field not in ALLOWED_SLIDE_FIELDS:
            issues.append(f"slide[{idx}]: unknown field '{field}'")

    # コンテンツの空チェック
    if slide.get('title', '') == '' and slide.get('source_file', '') != 'course_title':
        issues.append(f"slide[{idx}]: title is empty")

    return issues


def validate_chunk(data: Dict[str, Any], filepath: str) -> Tuple[int, int, List[str]]:
    """
    チャンクJSONを検証

    Returns:
        (slide_count, issue_count, issues)
    """
    issues = []
    filename = os.path.basename(filepath)

    # エラーチャンクの場合
    if 'error' in data:
        issues.append(f"{filename}: error chunk - {data['error']}")
        return (0, len(issues), issues)

    # トップレベルフィールドチェック（配列形式とオブジェクト形式の両方を受け入れ）
    if isinstance(data, list):
        slides = data
    elif isinstance(data, dict) and 'slides' in data:
        if not isinstance(data['slides'], list):
            issues.append(f"{filename}: 'slides' must be an array")
            return (0, len(issues), issues)
        slides = data['slides']
    else:
        issues.append(f"{filename}: missing 'slides' array (expected {{\"slides\": [...]}} or [...])")
        return (0, len(issues), issues)

    # 各スライドを検証
    for idx, slide in enumerate(slides):
        slide_issues = validate_slide(slide, idx)
        issues.extend([f"{filename}: {issue}" for issue in slide_issues])

    # slide_numberの連番チェック
    slide_numbers = [s.get('slide_number') for s in slides if 'slide_number' in s]
    if slide_numbers:
        for i in range(1, len(slide_numbers)):
            if slide_numbers[i] is not None and slide_numbers[i - 1] is not None:
                if slide_numbers[i] <= slide_numbers[i - 1]:
                    issues.append(
                        f"{filename}: slide_number not ascending at index {i} "
                        f"({slide_numbers[i-1]} -> {slide_numbers[i]})"
                    )

    # total_slidesの整合性
    if 'total_slides' in data:
        if data['total_slides'] != len(slides):
            issues.append(
                f"{filename}: total_slides ({data['total_slides']}) != "
                f"actual slide count ({len(slides)})"
            )

    return (len(slides), len(issues), issues)


def validate_merged(data: Dict[str, Any], filepath: str) -> Tuple[int, int, List[str]]:
    """slides_plan.json（統合済み）を検証"""
    issues = []
    filename = os.path.basename(filepath)

    # 配列形式とオブジェクト形式の両方を受け入れ
    if isinstance(data, list):
        slides = data
    elif isinstance(data, dict) and 'slides' in data:
        slides = data['slides']
    else:
        issues.append(f"{filename}: missing 'slides' array (expected {{\"slides\": [...]}} or [...])")
        return (0, len(issues), issues)

    for idx, slide in enumerate(slides):
        slide_issues = validate_slide(slide, idx)
        issues.extend([f"{filename}: {issue}" for issue in slide_issues])

    # グローバルslide_number重複チェック
    slide_numbers = [s.get('slide_number') for s in slides]
    seen = set()
    for num in slide_numbers:
        if num in seen:
            issues.append(f"{filename}: duplicate slide_number {num}")
        seen.add(num)

    return (len(slides), len(issues), issues)


def main():
    parser = argparse.ArgumentParser(
        description='Validate slide JSON files before Phase 2'
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--file', help='Single JSON file to validate')
    group.add_argument('--dir', help='Directory containing JSON files to validate')
    args = parser.parse_args()

    files = []
    if args.file:
        files = [args.file]
    else:
        files = sorted(glob.glob(os.path.join(args.dir, '*.json')))

    if not files:
        print("Error: No JSON files found")
        sys.exit(1)

    print("=" * 60)
    print("nano-banana JSON Validation")
    print("=" * 60)

    total_slides = 0
    total_issues = 0
    all_issues = []

    for filepath in files:
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            msg = f"{os.path.basename(filepath)}: invalid JSON - {e}"
            all_issues.append(msg)
            total_issues += 1
            print(f"  FAIL {os.path.basename(filepath)}: invalid JSON")
            continue

        # slides_plan.json vs chunk_*.json で検証ロジックを分岐
        basename = os.path.basename(filepath)
        if basename == 'slides_plan.json':
            slides, issues_count, issues = validate_merged(data, filepath)
        else:
            slides, issues_count, issues = validate_chunk(data, filepath)

        total_slides += slides
        total_issues += issues_count
        all_issues.extend(issues)

        status = 'PASS' if issues_count == 0 else 'FAIL'
        print(f"  {status} {basename}: {slides} slides, {issues_count} issues")

    # サマリー
    print(f"\n{'='*60}")
    print("Validation Summary")
    print("=" * 60)
    print(f"Files checked:   {len(files)}")
    print(f"Total slides:    {total_slides}")
    print(f"Total issues:    {total_issues}")

    if all_issues:
        print(f"\nIssues:")
        for issue in all_issues:
            print(f"  ! {issue}")
        print("=" * 60)
        sys.exit(1)
    else:
        print("\nAll validations passed.")
        print("=" * 60)
        sys.exit(0)


if __name__ == '__main__':
    main()
