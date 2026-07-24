#!/usr/bin/env python3
"""TEKION Slide Generator v6 - 差分編集スクリプト（Phase 8）

生成済みスライドを「ゼロから作り直す」のではなく、現行画像を参照にして
指摘箇所だけを直す:

  - レイアウト・配色・他のテキストを保ったまま部分修正できる
  - 元画像は上書きせずバージョン保存（<base>_v2.png, _v3.png ...）
  - manifest の current_image が指す「確定版」を差し替える（export は常に確定版を使う）
  - ロゴ・フッターは raw（焼き込み前画像）から編集して決定的に再合成する
    （焼き込み済み画像を参照にすると、モデルがロゴを「再描画」して崩れるため）

3つの編集モード:
  1. 指示編集（--instruction）: 現行スライドを参照画像に渡し、
     「指示された変更のみ適用、他は同一に保て」と指示する
  2. 赤ペン編集（--annotated）: 赤で注釈を書き込んだ画像を唯一の参照として渡す。
     きれいな元画像を併送すると、モデルが無編集の元画像を返しがちなため、
     注釈入り1枚だけを渡す
  3. 作り直し（--rebuild）: 前の画像を一切参照せず、元の生成プロンプト（+指示）から
     新しいデザインで再生成する。「ガラッと変えたい」とき用。バージョン保存・
     ロールバックは通常編集と同じに効く

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
    parser.add_argument('--rebuild', action='store_true',
                        help='前の画像を参照せず、元の生成プロンプト（+指示）から作り直す')
    parser.add_argument('--reference-image',
                        help='追加の参照画像（キャラクター・ユーザー添付画像等。'
                             '--rebuild では唯一の参照、指示編集では現行画像に加えて渡される）')
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
    # raw も同じ版に切り替える（current だけ戻すと次回編集が別版の raw を参照してしまう）
    raw_candidate = os.path.join(os.path.dirname(prev), 'raw', os.path.basename(prev))
    update_entry(manifest, slide_base, current_image=prev, state='validated',
                 raw_image=raw_candidate if os.path.exists(raw_candidate) else None)
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

    if not args.instruction and not args.annotated and not args.rebuild:
        print("❌ --instruction / --annotated / --rebuild のいずれかを指定してください")
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

    if args.rebuild:
        # 作り直し: 前の画像を参照せず、元の生成プロンプト（+指示）から再生成する。
        # デザイン・構成をガラッと変えたいとき、前の画像の参照が邪魔になるため。
        # prompt_file が古い（セッション移動等）場合はセッション内の prompts/ に落ちる
        prompt_candidates = [entry.get('prompt_file'),
                             os.path.join(session_dir, 'prompts', f"{slide_base}.txt")]
        prompt_file = next((p for p in prompt_candidates if p and os.path.exists(p)), None)
        base_prompt = None
        if prompt_file:
            with open(prompt_file, 'r', encoding='utf-8') as f:
                base_prompt = f.read()
        if base_prompt:
            prompt = base_prompt
            if args.instruction:
                prompt += ("\n\n[作り直しにあたっての指示]\n"
                           f"{args.instruction}\n"
                           "この指示を反映し、前とは異なる新しいデザイン・構成で作ること。")
        elif args.instruction:
            # 取り込みスライド等プロンプトが無い場合は、指示そのものから作る
            prompt = EDIT_PROMPT_TEMPLATE.format(instruction=args.instruction).replace(
                "添付の参照画像は「現在のスライド」です。これを編集して完成版を返してください。",
                "16:9 の日本語プレゼンテーションスライドを新規に1枚生成してください。")
        else:
            print(f"❌ 作り直し不可: {slide_base} の生成プロンプトが見つかりません"
                  f"（--instruction で作り直しの内容を指定してください）")
            return 1
        if args.reference_image:
            if not os.path.exists(args.reference_image):
                print(f"❌ 参照画像が見つかりません: {args.reference_image}")
                return 1
            reference = args.reference_image
        else:
            # 初回生成で使ったアセット参照（キャラクター等）を自動継承する
            reference = None
            map_path = os.path.join(session_dir, 'reference_image_map.json')
            if os.path.exists(map_path):
                import json as _json
                try:
                    with open(map_path, 'r', encoding='utf-8') as f:
                        ref_map = _json.load(f)
                except (_json.JSONDecodeError, OSError):
                    ref_map = {}
                cand = ref_map.get(slide_base)
                if not cand:
                    for pattern, image_path in ref_map.items():
                        if pattern in slide_base or slide_base in pattern:
                            cand = image_path
                            break
                if cand and os.path.exists(cand):
                    reference = cand
        edit_from_raw = True  # 新規生成と同じ扱い: raw を保存しロゴ・フッターを再合成する
    elif args.annotated:
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
        if args.reference_image and not os.path.exists(args.reference_image):
            print(f"❌ 参照画像が見つかりません: {args.reference_image}")
            return 1

    versions = entry.get('versions', [])
    output_path, version_no = next_version_path(images_dir, slide_base, versions)

    mode_label = "作り直し" if args.rebuild else "差分編集"
    print(f"🎨 {mode_label}: {slide_base} → v{version_no} ({os.path.basename(output_path)})")
    if args.rebuild:
        print("   参照: なし（前の画像を参照せず再生成）"
              + (f" + アセット参照 {os.path.basename(reference)}" if reference else ""))
    else:
        print(f"   参照: {os.path.basename(reference)}"
              + (" (raw)" if edit_from_raw else "")
              + (" [赤ペン編集]" if args.annotated else ""))

    from providers import get_provider, ImageRequest

    # 指示編集で --reference-image が渡された場合は、現行画像に加えて参照に含める
    # （「この画像のように」「このキャラを入れて」等のユーザー添付画像）
    extra_refs = ([args.reference_image]
                  if (not args.rebuild and not args.annotated and args.reference_image)
                  else [])
    if extra_refs:
        prompt += ("\n\n[参照画像の読み方]\n"
                   "最後の参照画像が「現在のスライド」。それ以外はユーザーが添付した参考資料"
                   "（キャラクター・見本レイアウト・使ってほしい写真等）で、指示に従って反映すること。")

    request = ImageRequest(
        prompt=prompt,
        output_path=output_path,
        api_key="",
        image_size=args.image_size,
        max_retries=args.max_retries,
        reference_image_path=reference,
        reference_images=extra_refs,
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
    # rebuild で新 raw が無い場合（プロバイダが raw 非対応等）は旧 raw を残さない:
    # 旧デザインの raw を次回編集で参照してしまうため
    update_entry(manifest, slide_base,
                 state='validated',
                 current_image=output_path,
                 raw_image=new_raw if os.path.exists(new_raw)
                 else (None if args.rebuild else entry.get('raw_image')),
                 versions=versions + [output_path],
                 last_edit_instruction=('(rebuild) ' if args.rebuild else '')
                 + (args.instruction[:300] if args.instruction
                    else ('' if args.rebuild else '(mark edit)')))
    save_manifest(manifest_path, manifest)

    print(f"✅ 編集完了: {os.path.basename(output_path)} が確定版になりました")
    print(f"   気に入らなければ: python3 edit_slide.py --session-dir {session_dir} "
          f"--slide {slide_base} --rollback")
    return 0


if __name__ == '__main__':
    sys.exit(main())
