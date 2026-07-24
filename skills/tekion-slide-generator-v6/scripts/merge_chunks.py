#!/usr/bin/env python3
"""
nano-banana Slide Generator - Chunk Merge & Renumber Script

複数SubAgentが生成したchunk_*.jsonを統合し、slide_numberをグローバル連番に振り直す。
jqコマンドの代替として使用。

Usage:
    python merge_chunks.py --input-dir slides_output/json --output slides_output/json/slides_plan.json
"""

import argparse
import glob
import json
import os
import sys


def natural_sort_key(filepath):
    """chunk_0.json, chunk_1.json, ... chunk_10.json を正しくソート"""
    import re
    basename = os.path.basename(filepath)
    parts = re.split(r'(\d+)', basename)
    return [int(p) if p.isdigit() else p.lower() for p in parts]


def merge_and_renumber(input_dir, output_path):
    chunk_files = sorted(
        glob.glob(os.path.join(input_dir, 'chunk_*.json')),
        key=natural_sort_key
    )

    if not chunk_files:
        print(f"Error: No chunk_*.json files found in {input_dir}")
        sys.exit(1)

    print(f"Found {len(chunk_files)} chunk files")

    all_slides = []
    has_course_title = False

    for filepath in chunk_files:
        basename = os.path.basename(filepath)
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  WARN: Skipping {basename} - {e}")
            continue

        if 'error' in data:
            print(f"  WARN: Skipping {basename} - error chunk: {data['error']}")
            continue

        slides = data.get('slides', [])
        print(f"  {basename}: {len(slides)} slides")

        for slide in slides:
            if slide.get('source_file') == 'course_title':
                has_course_title = True
            all_slides.append(slide)

    if not all_slides:
        print("Error: No slides found in any chunk file")
        sys.exit(1)

    # リナンバー: course_title=0, それ以降は1から連番
    renumbered = []
    next_number = 0 if has_course_title else 1

    # course_titleスライドを先頭に配置
    if has_course_title:
        for slide in all_slides:
            if slide.get('source_file') == 'course_title':
                renumbered.append({**slide, 'slide_number': 0})
                break
        next_number = 1

    # 残りのスライドを順番に追加
    for slide in all_slides:
        if slide.get('source_file') == 'course_title':
            continue
        renumbered.append({**slide, 'slide_number': next_number})
        next_number += 1

    # 出力
    output_data = {'slides': renumbered}

    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    print(f"\nMerged: {len(renumbered)} slides → {output_path}")
    if has_course_title:
        print(f"  slide_number: 0 (course_title) + 1-{len(renumbered)-1}")
    else:
        print(f"  slide_number: 1-{len(renumbered)}")


def main():
    parser = argparse.ArgumentParser(
        description='Merge chunk JSON files and renumber slides globally'
    )
    parser.add_argument(
        '--input-dir',
        required=True,
        help='Directory containing chunk_*.json files'
    )
    parser.add_argument(
        '--output',
        required=True,
        help='Output path for merged slides_plan.json'
    )
    args = parser.parse_args()

    print("=" * 60)
    print("nano-banana Chunk Merge & Renumber")
    print("=" * 60)

    merge_and_renumber(args.input_dir, args.output)

    print("=" * 60)


if __name__ == '__main__':
    main()
