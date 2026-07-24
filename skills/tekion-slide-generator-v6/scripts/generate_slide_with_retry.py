#!/usr/bin/env python3
"""
nano-banana Slide Generator v3 Flash - 単一スライド画像生成スクリプト（リトライ機能付き）

Gemini 3.1 Flash Image Preview REST APIを使用して16:9スライド画像を生成します。
API失敗時は自動的にリトライします。

v3 Flash 新機能:
  - モデル: gemini-3.1-flash-image-preview (Nano Banana 2)
  - Thinking: thinkingLevel制御 (minimal/High)
  - グラウンディング: Google画像検索によるリアルタイム参照
  - 解像度: 512px / 1K / 2K / 4K 選択可能

使用方法:
    python generate_slide_with_retry.py --prompt "<プロンプト>" --output "<出力パス>"

環境変数:
    GEMINI_API_KEY: Gemini APIキー（必須）
"""
import os
import sys
import argparse
import json
import requests
import base64
import time
from typing import Optional


MODEL_ID = "gemini-3.1-flash-image-preview"
API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
VALID_IMAGE_SIZES = {"512px", "1K", "2K", "4K"}
DEFAULT_IMAGE_SIZE = "2K"
DEFAULT_THINKING_LEVEL = "High"
REQUEST_TIMEOUT = 120


def build_payload(
    prompt: str,
    logo_data: Optional[str] = None,
    reference_image_data: Optional[str] = None,
    reference_image_mime: str = "image/jpeg",
    image_size: str = DEFAULT_IMAGE_SIZE,
    thinking_level: str = DEFAULT_THINKING_LEVEL,
    grounding: bool = False,
) -> dict:
    """
    Gemini API リクエストペイロードを構築する

    Args:
        prompt: 画像生成プロンプト
        logo_data: ロゴ画像のBase64エンコード文字列
        reference_image_data: リファレンス画像のBase64エンコード文字列
        reference_image_mime: リファレンス画像のMIMEタイプ
        image_size: 出力解像度 (512px, 1K, 2K, 4K)
        thinking_level: Thinkingレベル (minimal, High)
        grounding: Google画像検索グラウンディングの有効/無効
    """
    parts = [{"text": prompt}]
    if logo_data:
        parts.append({
            "inlineData": {
                "mimeType": "image/png",
                "data": logo_data
            }
        })
    if reference_image_data:
        parts.append({
            "inlineData": {
                "mimeType": reference_image_mime,
                "data": reference_image_data
            }
        })

    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            "imageConfig": {
                "aspectRatio": "16:9",
                "imageSize": image_size
            },
            "thinkingConfig": {
                "thinkingLevel": thinking_level,
                "includeThoughts": False
            }
        },
        "safetySettings": [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_ONLY_HIGH"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_ONLY_HIGH"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_ONLY_HIGH"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_ONLY_HIGH"},
        ]
    }

    if grounding:
        payload["tools"] = [{
            "googleSearch": {
                "searchTypes": {
                    "webSearch": {},
                    "imageSearch": {}
                }
            }
        }]

    return payload


def extract_image_from_response(result: dict) -> Optional[bytes]:
    """
    APIレスポンスから最終画像データを抽出する

    includeThoughts=Falseの場合、APIは中間思考画像を返さないため、
    レスポンス内のinlineDataはすべて最終画像として扱う。
    複数のinlineDataがある場合は末尾を採用する（最終出力が末尾に来る）。
    """
    if "candidates" not in result or not result["candidates"]:
        return None

    candidate = result["candidates"][0]
    if "content" not in candidate or "parts" not in candidate["content"]:
        return None

    final_image_data = None
    for part in candidate["content"]["parts"]:
        if "inlineData" in part:
            final_image_data = base64.b64decode(part["inlineData"]["data"])

    return final_image_data


def extract_grounding_metadata(result: dict) -> Optional[dict]:
    """APIレスポンスからグラウンディングメタデータを抽出する"""
    if "candidates" not in result or not result["candidates"]:
        return None

    candidate = result["candidates"][0]
    return candidate.get("groundingMetadata")


def save_grounding_metadata(output_path: str, grounding_meta: dict) -> None:
    """グラウンディングメタデータをJSONファイルとして保存する"""
    slide_name = os.path.splitext(os.path.basename(output_path))[0]
    grounding_dir = os.path.join(os.path.dirname(output_path), '..', 'grounding')
    os.makedirs(grounding_dir, exist_ok=True)
    grounding_file = os.path.join(grounding_dir, f"{slide_name}_grounding.json")
    with open(grounding_file, 'w', encoding='utf-8') as f:
        json.dump(grounding_meta, f, ensure_ascii=False, indent=2)


def generate_slide_with_retry(
    prompt: str,
    output_path: str,
    api_key: str,
    max_retries: int = 3,
    retry_delay: float = 2.0,
    logo_data: Optional[str] = None,
    reference_image_data: Optional[str] = None,
    reference_image_mime: str = "image/jpeg",
    image_size: str = DEFAULT_IMAGE_SIZE,
    thinking_level: str = DEFAULT_THINKING_LEVEL,
    grounding: bool = False,
) -> bool:
    """
    Gemini REST API を使用してスライド画像を生成（リトライ機能付き）
    """
    slide_name = os.path.basename(output_path)
    url = f"{API_BASE}/{MODEL_ID}:generateContent?key={api_key}"

    payload = build_payload(
        prompt=prompt,
        logo_data=logo_data,
        reference_image_data=reference_image_data,
        reference_image_mime=reference_image_mime,
        image_size=image_size,
        thinking_level=thinking_level,
        grounding=grounding,
    )

    for attempt in range(1, max_retries + 1):
        try:
            if attempt > 1:
                wait_time = retry_delay * (2 ** (attempt - 2))
                print(f"⏳ リトライ {attempt}/{max_retries} (待機: {wait_time:.1f}秒): {slide_name}", file=sys.stderr)
                time.sleep(wait_time)
            else:
                grounding_label = " [Grounding]" if grounding else ""
                print(f"🎨 スライド生成開始: {slide_name} ({image_size}{grounding_label})", file=sys.stderr)

            response = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)

            if response.status_code == 429:
                print(f"⚠️  レート制限エラー (429): {slide_name}", file=sys.stderr)
                if attempt < max_retries:
                    continue
                return False

            if response.status_code == 503:
                print(f"⚠️  サービス一時停止 (503): {slide_name}", file=sys.stderr)
                if attempt < max_retries:
                    continue
                return False

            if response.status_code != 200:
                print(f"❌ APIエラー ({response.status_code}): {slide_name}", file=sys.stderr)
                print(f"   詳細: {response.text[:300]}", file=sys.stderr)
                if attempt < max_retries:
                    continue
                return False

            result = response.json()

            image_data = extract_image_from_response(result)
            if image_data is None:
                print(f"❌ エラー: レスポンスに画像データなし: {slide_name}", file=sys.stderr)
                if attempt < max_retries:
                    continue
                return False

            output_dir = os.path.dirname(output_path)
            if output_dir:
                os.makedirs(output_dir, exist_ok=True)

            from footer_utils import apply_footer_to_bytes
            image_data = apply_footer_to_bytes(image_data, output_format="PNG")

            with open(output_path, 'wb') as f:
                f.write(image_data)

            if grounding:
                grounding_meta = extract_grounding_metadata(result)
                if grounding_meta:
                    save_grounding_metadata(output_path, grounding_meta)

            file_size = len(image_data)
            retry_info = f" (リトライ {attempt}回目)" if attempt > 1 else ""
            print(f"✅ 成功{retry_info}: {slide_name} ({file_size:,} bytes)", file=sys.stderr)
            return True

        except requests.exceptions.Timeout:
            print(f"⚠️  タイムアウト: {slide_name}", file=sys.stderr)
            if attempt < max_retries:
                continue
            return False

        except requests.exceptions.RequestException as e:
            print(f"⚠️  リクエストエラー: {slide_name} - {e}", file=sys.stderr)
            if attempt < max_retries:
                continue
            return False

        except Exception as e:
            print(f"❌ 予期しないエラー: {slide_name} - {type(e).__name__}: {e}", file=sys.stderr)
            if attempt < max_retries:
                continue
            return False

    print(f"❌ 最終失敗 ({max_retries}回リトライ): {slide_name}", file=sys.stderr)
    return False


def _run_gemini(args) -> bool:
    """Gemini 既存パス（v3-flash互換）で生成する。"""
    api_key = args.api_key or os.environ.get('GEMINI_API_KEY')
    if not api_key:
        print("❌ エラー: GEMINI_API_KEYが設定されていません", file=sys.stderr)
        print("  export GEMINI_API_KEY='your-api-key'", file=sys.stderr)
        sys.exit(1)

    logo_data = None
    if args.logo:
        if os.path.exists(args.logo):
            with open(args.logo, 'rb') as f:
                logo_data = base64.b64encode(f.read()).decode('utf-8')
            print(f"✓ ロゴ読み込み: {args.logo}", file=sys.stderr)
        else:
            print(f"⚠️ ロゴファイル未検出: {args.logo}", file=sys.stderr)

    reference_image_data = None
    reference_image_mime = "image/jpeg"
    if args.reference_image:
        if os.path.exists(args.reference_image):
            ext = os.path.splitext(args.reference_image)[1].lower()
            mime_map = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}
            reference_image_mime = mime_map.get(ext, 'image/jpeg')
            with open(args.reference_image, 'rb') as f:
                reference_image_data = base64.b64encode(f.read()).decode('utf-8')
            print(f"✓ リファレンス画像読み込み: {args.reference_image}", file=sys.stderr)
        else:
            print(f"⚠️ リファレンス画像未検出: {args.reference_image}", file=sys.stderr)

    return generate_slide_with_retry(
        prompt=args.prompt,
        output_path=args.output,
        api_key=api_key,
        max_retries=args.max_retries,
        retry_delay=args.retry_delay,
        logo_data=logo_data,
        reference_image_data=reference_image_data,
        reference_image_mime=reference_image_mime,
        image_size=args.image_size,
        thinking_level=args.thinking_level,
        grounding=args.grounding,
    )


def _run_via_provider(args, provider_name: str) -> bool:
    """Provider抽象レイヤ経由で生成する（OpenAI / Codex）。"""
    from providers import get_provider, ImageRequest

    # Codex はサブスク枠で動くため API キー不要（むしろ OPENAI_API_KEY を外す）
    # mock はテスト用でキー不要
    if provider_name in ("codex", "mock"):
        api_key = ""
    else:
        env_map = {"openai": "OPENAI_API_KEY"}
        env_name = env_map.get(provider_name, "")
        api_key = args.api_key or (os.environ.get(env_name) if env_name else None)
        if not api_key:
            print(f"❌ エラー: {env_name}が設定されていません", file=sys.stderr)
            print(f"  export {env_name}='your-api-key'", file=sys.stderr)
            sys.exit(1)

    request = ImageRequest(
        prompt=args.prompt,
        output_path=args.output,
        api_key=api_key,
        image_size=args.image_size,
        max_retries=args.max_retries,
        retry_delay=args.retry_delay,
        logo_path=args.logo,
        reference_image_path=args.reference_image,
        reference_images=list(args.extra_reference_image or []),
        thinking_level=args.thinking_level if args.thinking_level != DEFAULT_THINKING_LEVEL else None,
        grounding=args.grounding,
        quality=args.quality,
        input_fidelity=args.input_fidelity,
        background=args.background,
        raw_dir=args.raw_dir,
        skip_finish=args.skip_finish,
    )

    provider = get_provider(provider_name)
    response = provider.generate(request)
    return response.success


def main():
    parser = argparse.ArgumentParser(
        description='スライド画像を生成（Gemini / OpenAI 両対応・リトライ機能付き）',
    )
    parser.add_argument('--prompt', required=True, help='画像生成プロンプト')
    parser.add_argument('--output', required=True, help='出力画像パス')
    parser.add_argument('--provider', default='codex', choices=['gemini', 'openai', 'codex', 'mock'],
                        help='画像生成プロバイダ（gemini / openai / codex / mock=テスト用）')
    parser.add_argument('--api-key', help='APIキー（省略時は環境変数を使用）')
    parser.add_argument('--max-retries', type=int, default=3, help='最大リトライ回数（デフォルト: 3）')
    parser.add_argument('--retry-delay', type=float, default=2.0, help='リトライ間隔（秒）')
    parser.add_argument('--logo', help='ロゴ画像パス')
    parser.add_argument('--reference-image', help='リファレンス画像パス（スライドに含める参照画像）')
    parser.add_argument('--extra-reference-image', action='append', default=[],
                        help='追加の参照画像（複数指定可。スタイルアンカー等。v6）')
    parser.add_argument('--raw-dir',
                        help='ロゴ・フッター焼き込み前の素画像を保存するディレクトリ（差分編集用。v6）')
    parser.add_argument('--skip-finish', action='store_true',
                        help='ロゴ合成・フッター焼き込みをスキップ（16:9正規化のみ。v6）')
    parser.add_argument(
        '--image-size', default=DEFAULT_IMAGE_SIZE, choices=sorted(VALID_IMAGE_SIZES),
        help=f'出力解像度（デフォルト: {DEFAULT_IMAGE_SIZE}）'
    )
    # Gemini固有
    parser.add_argument(
        '--thinking-level', default=DEFAULT_THINKING_LEVEL, choices=['minimal', 'High'],
        help=f'[Gemini] Thinkingレベル（デフォルト: {DEFAULT_THINKING_LEVEL}）'
    )
    parser.add_argument('--grounding', action='store_true',
                        help='[Gemini] Google画像検索グラウンディングを有効化')
    # OpenAI固有
    parser.add_argument('--quality', default='medium', choices=['auto', 'low', 'medium', 'high'],
                        help='[OpenAI] 画質（デフォルト: medium。最高画質が必要なときのみ high を指定）')
    parser.add_argument('--input-fidelity', default='high', choices=['low', 'high'],
                        help='[OpenAI] 参考画像への忠実度（デフォルト: high）')
    parser.add_argument('--background', default='auto', choices=['auto', 'transparent', 'opaque'],
                        help='[OpenAI] 背景処理（デフォルト: auto）')

    args = parser.parse_args()

    if args.provider == 'gemini':
        success = _run_gemini(args)
    else:
        success = _run_via_provider(args, args.provider)
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
