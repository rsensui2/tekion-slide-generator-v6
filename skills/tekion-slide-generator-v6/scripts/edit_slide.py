#!/usr/bin/env python3
"""TEKION Slide Generator v6 - 差分編集スクリプト（Phase 8）

生成済みスライドを「ゼロから作り直す」のではなく、現行画像を参照にして
指摘箇所だけを直す:

  - レイアウト・配色・他のテキストを保ったまま部分修正できる
  - 元画像は上書きせずバージョン保存（<base>_v2.png, _v3.png ...）
  - manifest の current_image が指す「確定版」を差し替える（export は常に確定版を使う）
  - ロゴ・フッターは raw（焼き込み前画像）から編集して決定的に再合成する
    （焼き込み済み画像を参照にすると、モデルがロゴを「再描画」して崩れるため）

2つの編集モード:
  1. 指示編集（--instruction）: 現行スライドを参照画像に渡し、
     「指示された変更のみ適用、他は同一に保て」と指示する
  2. 赤ペン編集（--annotated）: 赤で注釈を書き込んだ画像を唯一の参照として渡す。
     きれいな元画像を併送すると、モデルが無編集の元画像を返しがちなため、
     注釈入り1枚だけを渡す

使用例:
    # 指示編集
    python edit_slide.py --session-dir <SESSION_DIR> --slide 02_solution_02 \
        --instruction "グラフの数値を 45% → 52% に修正" --logo <logo.png>

    # 赤ペン編集（注釈済みスクリーンショットを渡す）
    python edit_slide.py --session-dir <SESSION_DIR> --slide 02_solution_02 \
        --annotated /path/to/annotated.png --instruction "この領域を簡素化"

    # ロールバック
    python edit_slide.py --session-dir <SESSION_DIR> --slide 02_solution_02 --rollback
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from manifest_utils import (
    get_entry, load_manifest, next_version_path, save_manifest, update_entry,
    validate_image,
)

EDIT_PROMPT_TEMPLATE = """あなたはプレゼンテーションスライドの編集者です。
添付の参照画像は「現在のスライド」です。これを編集して完成版を返してください。

[編集指示]
{instruction}

[編集の規律]
- 指示された変更のみを適用すること。
- レイアウト・配色・フォントの性格・他のテキスト・図形は、参照画像と視覚的に同一に保つこと。
- 16:9 のアスペクト比を厳守。テキストは鮮明・正確なスペルで描画する。
- マークダウン記号（*, #, - など）を文字として描画しない。
"""

MARK_EDIT_PROMPT_TEMPLATE = """あなたはプレゼンテーションスライドの編集者です。
添付の1枚の画像は「現在のスライドに赤い注釈レイヤーを重ねたもの」です。

[注釈の読み方]
- 赤い線・赤い枠・赤い文字は「編集指示」であり、スライドの内容ではない。
- 注釈が示す領域に、指示に従った明確に目に見える変更を加えること。
- 赤い注釈を消しただけの元画像を返すことは絶対に禁止。必ず実質的な変更を加える。
{instruction_block}
[出力の規律]
- 完成版には注釈・赤マーク・選択枠・指示文を一切含めない。
- 注釈領域の外は、参照画像と視覚的に同一に保つこと。
- 16:9 のアスペクト比を厳守。テキストは鮮明・正確なスペルで描画する。
"""


def parse_args():
    parser = argparse.ArgumentParser(description='Edit a generated slide differentially (v6)')
    parser.add_argument('--session-dir', required=True, help='セッションディレクトリ')
    parser.add_argument('--slide', required=True, help='スライドベース名（例: 02_solution_02）')
    parser.add_argument('--instruction', default='', help='編集指示（自然言語）')
    parser.add_argument('--annotated', help='赤ペン注釈済み画像パス（指定時は赤ペン編集モード）')
    parser.add_argument('--rollback', action='store_true', help='1つ前のバージョンに戻す')
    parser.add_argument('--provider', default='codex', choices=['codex', 'openai', 'mock'],
                        help='画像生成プロバイダ（デフォルト: codex）')
    parser.add_argument('--image-size', default='2K', choices=['512px', '1K', '2K', '4K'])
    parser.add_argument('--logo', help='ロゴ画像パス（編集後に決定的に再合成する）')
    parser.add_argument('--manifest', help='manifest パス（デフォルト: <session-dir>/manifest.json)')
    parser.add_argument('--max-retries', type=int, default=2)
    return parser.parse_args()


def rollback(manifest: dict, manifest_path: str, slide_base: str) -> int:
    entry = get_entry(manifest, slide_base)
    versions = entry.get('versions', [])
    current = entry.get('current_image')
    if len(versions) < 2 or current not in versions:
        print(f"❌ ロールバック不可: {slide_base} にはバージョンが1つしかありません")
        return 1
    idx = versions.index(current)
    if idx == 0:
        print(f"❌ ロールバック不可: {slide_base} は既に最古のバージョンです")
        return 1
    prev = versions[idx - 1]
    if validate_image(prev) is not None:
        print(f"❌ ロールバック先が破損しています: {prev}")
        return 1
    update_entry(manifest, slide_base, current_image=prev, state='validated')
    save_manifest(manifest_path, manifest)
    print(f"↩️  ロールバック完了: {slide_base} → {os.path.basename(prev)}")
    print("   (export は manifest の current_image を使うため、次回 export から反映されます)")
    return 0


def main():
    args = parse_args()
    session_dir = os.path.abspath(args.session_dir)
    manifest_path = args.manifest or os.path.join(session_dir, 'manifest.json')
    images_dir = os.path.join(session_dir, 'images')
    raw_dir = os.path.join(images_dir, 'raw')

    manifest = load_manifest(manifest_path)
    slide_base = args.slide
    entry = get_entry(manifest, slide_base)

    if args.rollback:
        return rollback(manifest, manifest_path, slide_base)

    if not args.instruction and not args.annotated:
        print("❌ --instruction か --annotated のどちらかを指定してください")
        return 1

    current = entry.get('current_image')
    if not current or not os.path.exists(current):
        # manifest に無い場合はディスクから推定（旧セッションとの互換）
        candidate = os.path.join(images_dir, f"{slide_base}.png")
        if os.path.exists(candidate):
            current = candidate
            entry.setdefault('versions', []).append(candidate)
        else:
            print(f"❌ スライドが見つかりません: {slide_base}（manifest にも {candidate} にも無い）")
            return 1

    # 参照画像の選択:
    #   赤ペン編集 → 注釈画像のみ（クリーン画像を併送すると無編集で返りがち）
    #   指示編集   → raw（ロゴ・フッター焼き込み前）を優先。無ければ焼き込み済み現行画像
    raw_image = entry.get('raw_image')
    if raw_image and not os.path.exists(raw_image):
        raw_image = None
    if not raw_image:
        candidate = os.path.join(raw_dir, os.path.basename(current))
        raw_image = candidate if os.path.exists(candidate) else None

    if args.annotated:
        if not os.path.exists(args.annotated):
            print(f"❌ 注釈画像が見つかりません: {args.annotated}")
            return 1
        reference = args.annotated
        instruction_block = (
            f"\n[作者の指示]\n{args.instruction}\n" if args.instruction
            else "\n注釈が領域の指定のみの場合は、その領域をスライド内での役割を保ったまま改善・簡素化すること。\n"
        )
        prompt = MARK_EDIT_PROMPT_TEMPLATE.format(instruction_block=instruction_block)
        # 注釈は焼き込み済み画像に書かれるため、仕上げ再適用はしない（既に焼き込み済みの見た目を保持）
        edit_from_raw = False
    else:
        reference = raw_image or current
        prompt = EDIT_PROMPT_TEMPLATE.format(instruction=args.instruction)
        edit_from_raw = raw_image is not None
        if not edit_from_raw:
            print("⚠️  raw 画像が無いため、焼き込み済み画像から編集します"
                  "（ロゴ・フッターの再合成はスキップし、参照画像の再現に任せます）")

    versions = entry.get('versions', [])
    output_path, version_no = next_version_path(images_dir, slide_base, versions)

    print(f"🎨 差分編集: {slide_base} → v{version_no} ({os.path.basename(output_path)})")
    print(f"   参照: {os.path.basename(reference)}"
          + (" (raw)" if edit_from_raw else "")
          + (" [赤ペン編集]" if args.annotated else ""))

    from providers import get_provider, ImageRequest

    request = ImageRequest(
        prompt=prompt,
        output_path=output_path,
        api_key="",
        image_size=args.image_size,
        max_retries=args.max_retries,
        reference_image_path=reference,
        # raw から編集した場合のみロゴ・フッターを決定的に再合成する。
        # 焼き込み済み画像からの編集では二重焼き込みになるためスキップ。
        logo_path=args.logo if edit_from_raw else None,
        skip_finish=not edit_from_raw,
        raw_dir=raw_dir if edit_from_raw else None,
        input_fidelity="high",
    )

    provider = get_provider(args.provider)
    response = provider.generate(request)
    if not response.success:
        print(f"❌ 編集失敗: {response.error}")
        return 1

    problem = validate_image(output_path)
    if problem:
        print(f"❌ 生成画像が検証に失敗: {problem}")
        try:
            os.unlink(output_path)  # 破損版を versions に入れない
        except OSError:
            pass
        return 1

    new_raw = os.path.join(raw_dir, os.path.basename(output_path))
    update_entry(manifest, slide_base,
                 state='validated',
                 current_image=output_path,
                 raw_image=new_raw if os.path.exists(new_raw) else entry.get('raw_image'),
                 versions=versions + [output_path],
                 last_edit_instruction=args.instruction[:300] if args.instruction else '(mark edit)')
    save_manifest(manifest_path, manifest)

    print(f"✅ 編集完了: {os.path.basename(output_path)} が確定版になりました")
    print(f"   気に入らなければ: python3 edit_slide.py --session-dir {session_dir} "
          f"--slide {slide_base} --rollback")
    return 0


if __name__ == '__main__':
    sys.exit(main())
