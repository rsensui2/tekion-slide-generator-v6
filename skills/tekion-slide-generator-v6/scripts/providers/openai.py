"""OpenAI GPT-image-2 プロバイダ実装。

- 参考画像なし: POST /v1/images/generations
- 参考画像あり: POST /v1/images/edits（最大16枚、multipart/form-data）

GPT-image-2の特徴:
- 16:9ネイティブ対応（1920x1080, 2560x1440, 3840x2160 等）
- 任意解像度（両辺16px倍数 / 長辺3:1以内 / 総ピクセル 655,360〜8,294,400）
- input_fidelity: "high" で参考画像への忠実度を強化
- レスポンスは b64_json で返却

環境変数:
    OPENAI_API_KEY: OpenAI APIキー
"""
from __future__ import annotations

import base64
import os
import sys
import time
from typing import Optional

import requests

from .base import Capability, ImageProvider, ImageRequest, ImageResponse


MODEL_ID = "gpt-image-2"
API_BASE = "https://api.openai.com/v1"
REQUEST_TIMEOUT = 180

# Tier 3 想定: 50 IPM → 並列10で十分実用（余裕をもって）
DEFAULT_PARALLEL = 10

# 共通サイズラベル → GPT-image-2 ネイティブサイズ
# 制約: 両辺16pxの倍数 / 長辺3:1以内 / 総ピクセル 655,360〜8,294,400
# 注: 1920x1080 は 1080 が 16 倍数でないため使用不可 → 1792x1008 を採用
SIZE_MAP = {
    "512px": "1280x720",    # HD 16:9 (921K px, 下限超え)
    "1K":    "1792x1008",   # 16:9 exact (1,806K px)
    "2K":    "2560x1440",   # ≒16:9 (4,227K px, Gemini 2Kと完全一致)
    "4K":    "3840x2160",   # 4K UHD 16:9 (8,294K px, 上限)
}


def _mime_type(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(ext, "image/png")


class OpenAIImageProvider(ImageProvider):
    """OpenAI gpt-image-2 を使った画像生成プロバイダ。"""

    CAPABILITIES = Capability(
        name="openai",
        model=MODEL_ID,
        native_16_9=True,
        max_reference_images=16,
        supports_grounding=False,
        supports_thinking=False,
        supports_transparent_bg=True,
        default_parallel=DEFAULT_PARALLEL,
        size_map=SIZE_MAP,
    )

    def generate(self, request: ImageRequest) -> ImageResponse:
        slide_name = os.path.basename(request.output_path)
        reference_images = self.collect_reference_images(request)

        # 未対応フラグの警告（エラーにはしない）
        if request.grounding:
            print(f"⚠️  OpenAI: grounding非対応、スキップします: {slide_name}", file=sys.stderr)
        if request.thinking_level:
            print(f"⚠️  OpenAI: thinking_level非対応、スキップします: {slide_name}", file=sys.stderr)

        output_dir = os.path.dirname(request.output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        for attempt in range(1, request.max_retries + 1):
            try:
                if attempt > 1:
                    wait = request.retry_delay * (2 ** (attempt - 2))
                    print(f"⏳ リトライ {attempt}/{request.max_retries} (待機: {wait:.1f}秒): {slide_name}",
                          file=sys.stderr)
                    time.sleep(wait)
                else:
                    mode = "edits" if reference_images else "generations"
                    ref_info = f" refs={len(reference_images)}" if reference_images else ""
                    print(f"🎨 OpenAI生成開始: {slide_name} [{mode}]{ref_info}", file=sys.stderr)

                if reference_images:
                    result = self._call_edits(request, reference_images)
                else:
                    result = self._call_generations(request)

                if not result.get("ok"):
                    status = result.get("status", 0)
                    detail = result.get("error", "")
                    # 再試行可能ステータスのみ retry。他（400等）は即座に失敗返却
                    if status in (429, 500, 502, 503, 504):
                        print(f"⚠️  再試行可能エラー ({status}): {slide_name}", file=sys.stderr)
                        if attempt < request.max_retries:
                            continue
                    print(f"❌ OpenAI APIエラー ({status}): {slide_name}", file=sys.stderr)
                    print(f"   詳細: {detail[:300]}", file=sys.stderr)
                    return ImageResponse(
                        success=False,
                        output_path=request.output_path,
                        error=f"API error {status}: {detail[:300]}",
                        attempts=attempt,
                    )

                image_bytes = result["image_bytes"]

                import sys as _sys, os as _os
                _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
                from footer_utils import apply_footer_to_bytes
                image_bytes = apply_footer_to_bytes(image_bytes, output_format="PNG")

                with open(request.output_path, "wb") as f:
                    f.write(image_bytes)

                retry_info = f" (リトライ {attempt}回目)" if attempt > 1 else ""
                print(f"✅ 成功{retry_info}: {slide_name} ({len(image_bytes):,} bytes)",
                      file=sys.stderr)
                return ImageResponse(
                    success=True,
                    output_path=request.output_path,
                    metadata=result.get("metadata", {}),
                    attempts=attempt,
                )

            except requests.exceptions.Timeout:
                print(f"⚠️  タイムアウト: {slide_name}", file=sys.stderr)
                if attempt < request.max_retries:
                    continue
                return ImageResponse(
                    success=False,
                    output_path=request.output_path,
                    error="Timeout",
                    attempts=attempt,
                )
            except requests.exceptions.RequestException as e:
                print(f"⚠️  リクエストエラー: {slide_name} - {e}", file=sys.stderr)
                if attempt < request.max_retries:
                    continue
                return ImageResponse(
                    success=False,
                    output_path=request.output_path,
                    error=str(e),
                    attempts=attempt,
                )
            except Exception as e:
                print(f"❌ 予期しないエラー: {slide_name} - {type(e).__name__}: {e}",
                      file=sys.stderr)
                if attempt < request.max_retries:
                    continue
                return ImageResponse(
                    success=False,
                    output_path=request.output_path,
                    error=f"{type(e).__name__}: {e}",
                    attempts=attempt,
                )

        return ImageResponse(
            success=False,
            output_path=request.output_path,
            error=f"Exhausted {request.max_retries} retries",
            attempts=request.max_retries,
        )

    # ---------------------------------------------------------------
    # 内部: /v1/images/generations
    # ---------------------------------------------------------------
    def _call_generations(self, request: ImageRequest) -> dict:
        url = f"{API_BASE}/images/generations"
        size = self.CAPABILITIES.resolve_size(request.image_size)
        payload = {
            "model": MODEL_ID,
            "prompt": request.prompt,
            "size": size,
            "quality": request.quality,
            "output_format": "png",
            "background": request.background,
            "n": 1,
        }
        headers = {
            "Authorization": f"Bearer {request.api_key}",
            "Content-Type": "application/json",
        }
        response = requests.post(url, json=payload, headers=headers, timeout=REQUEST_TIMEOUT)
        return self._parse_response(response)

    # ---------------------------------------------------------------
    # 内部: /v1/images/edits
    # ---------------------------------------------------------------
    def _call_edits(self, request: ImageRequest, reference_paths: list[str]) -> dict:
        url = f"{API_BASE}/images/edits"
        size = self.CAPABILITIES.resolve_size(request.image_size)

        # OpenAIのeditsは最大16枚
        usable_refs = reference_paths[:self.CAPABILITIES.max_reference_images]

        files = []
        opened_files = []
        try:
            for path in usable_refs:
                fh = open(path, "rb")
                opened_files.append(fh)
                files.append(("image[]", (os.path.basename(path), fh, _mime_type(path))))

            # Note: input_fidelity は gpt-image-1.5 のみ対応。gpt-image-2 では送信しない。
            data = {
                "model": MODEL_ID,
                "prompt": request.prompt,
                "size": size,
                "quality": request.quality,
                "output_format": "png",
                "background": request.background,
                "n": "1",
            }
            headers = {"Authorization": f"Bearer {request.api_key}"}
            response = requests.post(
                url, data=data, files=files, headers=headers, timeout=REQUEST_TIMEOUT
            )
            return self._parse_response(response)
        finally:
            for fh in opened_files:
                try:
                    fh.close()
                except Exception:
                    pass

    # ---------------------------------------------------------------
    # 内部: レスポンスをパース
    # ---------------------------------------------------------------
    def _parse_response(self, response: requests.Response) -> dict:
        if response.status_code != 200:
            return {
                "ok": False,
                "status": response.status_code,
                "error": response.text,
            }
        try:
            data = response.json()
        except ValueError:
            return {"ok": False, "status": response.status_code, "error": "Invalid JSON"}

        items = data.get("data", [])
        if not items:
            return {"ok": False, "status": response.status_code, "error": "No data in response"}

        first = items[0]
        b64 = first.get("b64_json")
        if not b64:
            return {"ok": False, "status": response.status_code, "error": "No b64_json in response"}

        try:
            image_bytes = base64.b64decode(b64)
        except Exception as e:
            return {"ok": False, "status": response.status_code, "error": f"Base64 decode: {e}"}

        metadata = {
            "usage": data.get("usage"),
            "background": data.get("background"),
            "quality": data.get("quality"),
            "size": data.get("size"),
            "output_format": data.get("output_format"),
            "revised_prompt": first.get("revised_prompt"),
        }
        return {"ok": True, "image_bytes": image_bytes, "metadata": metadata}
