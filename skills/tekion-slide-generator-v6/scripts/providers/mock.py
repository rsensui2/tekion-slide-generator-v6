"""Mock 画像生成プロバイダ（テスト専用）。

API・サブスク枠を一切消費せずに、v6 パイプライン全体（manifest / 検証スイープ /
resume / 差分編集 / export）を E2E でテストするためのプロバイダ。
スライド名入りのプレースホルダ PNG を即時生成する。

障害注入（検証スイープのテスト用）:
    TEKION_MOCK_FAIL_SLIDES: カンマ区切りの部分一致パターン。マッチしたスライドは
                             最初の TEKION_MOCK_FAIL_TIMES 回（既定1回）失敗する。
                             失敗回数は出力ディレクトリの .mockfail_<base> で永続化
                             （子プロセスをまたいで数えるため）。
    TEKION_MOCK_FAIL_TIMES:  失敗させる回数（既定: 1 = 1回失敗して次で成功）
    TEKION_MOCK_FAIL_KIND:   失敗メッセージの種類 (retryable / rate_limited / auth_terminal)
    TEKION_MOCK_DELAY:       1枚あたりの擬似生成秒数（既定: 0.2）
"""
from __future__ import annotations

import os
import sys
import time
from io import BytesIO

from .base import Capability, ImageProvider, ImageRequest, ImageResponse

SIZE_MAP = {
    "512px": "1280x720",
    "1K":    "1792x1008",
    "2K":    "2560x1440",
    "4K":    "3840x2160",
}

FAIL_MESSAGES = {
    "retryable": "mock simulated transient error",
    "rate_limited": "mock simulated HTTP 429 rate limit",
    "auth_terminal": "mock simulated token_revoked — codex login required",
}


def _should_fail(output_path: str) -> str | None:
    """障害注入の判定。失敗させる場合はエラーメッセージを返す。"""
    patterns = [p.strip() for p in os.environ.get("TEKION_MOCK_FAIL_SLIDES", "").split(",") if p.strip()]
    if not patterns:
        return None
    base = os.path.splitext(os.path.basename(output_path))[0]
    if not any(p in base for p in patterns):
        return None

    fail_times = int(os.environ.get("TEKION_MOCK_FAIL_TIMES", "1"))
    marker = os.path.join(os.path.dirname(output_path) or ".", f".mockfail_{base}")
    count = 0
    if os.path.exists(marker):
        try:
            with open(marker, "r", encoding="utf-8") as f:
                count = int(f.read().strip() or "0")
        except (ValueError, OSError):
            count = 0
    if count >= fail_times:
        return None
    with open(marker, "w", encoding="utf-8") as f:
        f.write(str(count + 1))
    kind = os.environ.get("TEKION_MOCK_FAIL_KIND", "retryable")
    return FAIL_MESSAGES.get(kind, FAIL_MESSAGES["retryable"])


def _render_placeholder(slide_base: str, width: int, height: int, note: str = "") -> bytes:
    """スライド名入りのプレースホルダ画像を描画する（検証の白紙検知を通る程度の変化を持つ）。"""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    # 上部にブランド風の帯、左に縦ラインを描いて「単色でない」画像にする
    draw.rectangle([0, 0, width, int(height * 0.14)], fill=(16, 79, 158))
    draw.rectangle([0, int(height * 0.14), int(width * 0.02), height], fill=(240, 144, 33))
    draw.rectangle([int(width * 0.06), int(height * 0.30), int(width * 0.94), int(height * 0.86)],
                   outline=(200, 200, 200), width=4)
    text = f"MOCK SLIDE\n{slide_base}"
    if note:
        text += f"\n{note}"
    try:
        from PIL import ImageFont
        font = ImageFont.load_default(size=int(height * 0.05))
    except Exception:
        font = None  # 古い Pillow ではデフォルトサイズで描く（テスト用途なので十分）
    draw.multiline_text((int(width * 0.08), int(height * 0.38)), text,
                        fill=(30, 30, 30), font=font, spacing=16)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class MockImageProvider(ImageProvider):
    """テスト用: 即時ダミー生成・枠消費ゼロ・障害注入可能。"""

    CAPABILITIES = Capability(
        name="mock",
        model="mock (test placeholder)",
        native_16_9=True,
        max_reference_images=4,
        supports_grounding=False,
        supports_thinking=False,
        supports_transparent_bg=False,
        default_parallel=20,
        size_map=SIZE_MAP,
    )

    def generate(self, request: ImageRequest) -> ImageResponse:
        slide_base = os.path.splitext(os.path.basename(request.output_path))[0]

        fail_msg = _should_fail(request.output_path)
        if fail_msg:
            print(f"💥 mock: 注入された失敗: {slide_base} - {fail_msg}", file=sys.stderr)
            return ImageResponse(success=False, output_path=request.output_path,
                                 error=fail_msg, attempts=1)

        delay = float(os.environ.get("TEKION_MOCK_DELAY", "0.2"))
        if delay > 0:
            time.sleep(delay)

        spec = SIZE_MAP.get(request.image_size, "2560x1440")
        w, h = (int(x) for x in spec.split("x"))
        note_parts = []
        if request.reference_images or request.reference_image_path:
            refs = list(request.reference_images)
            if request.reference_image_path:
                refs.append(request.reference_image_path)
            note_parts.append(f"refs: {', '.join(os.path.basename(r) for r in refs if r)}")
        image_bytes = _render_placeholder(slide_base, w, h, note=" / ".join(note_parts))

        try:
            out_dir = os.path.dirname(request.output_path)
            if out_dir:
                os.makedirs(out_dir, exist_ok=True)

            if request.raw_dir:
                os.makedirs(request.raw_dir, exist_ok=True)
                raw_path = os.path.join(request.raw_dir, os.path.basename(request.output_path))
                with open(raw_path, "wb") as f:
                    f.write(image_bytes)

            if not request.skip_finish:
                if request.logo_path and os.path.exists(request.logo_path):
                    from .codex import _composite_logo
                    image_bytes = _composite_logo(image_bytes, request.logo_path)
                from footer_utils import apply_footer_to_bytes  # type: ignore
                image_bytes = apply_footer_to_bytes(image_bytes, output_format="PNG")

            with open(request.output_path, "wb") as f:
                f.write(image_bytes)
        except Exception as e:
            return ImageResponse(success=False, output_path=request.output_path,
                                 error=f"mock write error: {type(e).__name__}: {e}")

        print(f"✅ 成功 (mock): {slide_base}.png ({len(image_bytes):,} bytes)", file=sys.stderr)
        return ImageResponse(success=True, output_path=request.output_path,
                             metadata={"backend": "mock"})
