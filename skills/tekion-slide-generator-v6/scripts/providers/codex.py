"""Codex（ChatGPT/Codex サブスク枠）画像生成プロバイダ。

OpenAI API（従量課金）の代わりに、Codex CLI 内蔵の gpt-image-2 を使って
サブスクリプション枠で画像を生成する。生成自体は codex_app_server_client が担い、
本プロバイダは「スライドとしての仕上げ」を確定的に行う:

  1. Codex に 16:9 / 2K を指示して生成（codex_app_server_client.generate_image）
     参照画像（キャラクター等）は codex exec -i で gpt-image-2 に渡す
  2. 16:9 へ正規化（letterbox。テキスト欠けを避けるためクロップせずパッド）
  3. ロゴをローカル合成（Codex への添付に頼らず確実に焼き込む）
  4. フッターウォーターマーク焼き込み（footer_utils、既存と同一）

環境変数:
    CODEX_SLIDES_BACKEND: "exec"（既定）/ "app-server"
    CODEX_BIN:            codex 実行ファイル（既定 "codex"）
    ※ OPENAI_API_KEY はサブスク枠維持のため Codex 起動時に自動除去される
"""
from __future__ import annotations

import os
import sys
from io import BytesIO
from typing import Optional

from .base import Capability, ImageProvider, ImageRequest, ImageResponse


MODEL_ID = "gpt-image-2 (Codex subscription)"

# 実測: 並列20でも throttle なし（20枚/78秒、1K単純）。サブスク枠は枠消費が速い
# （公式: 通常の3-5倍）ため、累積 usage limit を避ける意味で既定は中庸の8。
# バースト生成なら --max-parallel 20 まで実証済み（枠残量に注意）。
DEFAULT_PARALLEL = 8

# 共通サイズラベル → 16:9 ターゲット解像度（OpenAI/Gemini と揃える）
# v6: 2K を 2560x1440 に修正（v5 の 2752x1536 は比率 1.792 で厳密な 16:9 ではなく、
# PPTX 配置時にわずかな歪み・レターボックスの原因になっていた）
SIZE_MAP = {
    "512px": "1280x720",
    "1K":    "1792x1008",
    "2K":    "2560x1440",
    "4K":    "3840x2160",
}


def _target_dims(size_label: str) -> tuple[int, int]:
    spec = SIZE_MAP.get(size_label, "2560x1440")
    w, h = spec.lower().split("x")
    return int(w), int(h)


def _normalize_to_16_9(image_bytes: bytes, target_w: int, target_h: int) -> bytes:
    """画像を 16:9 ターゲットへ正規化する。

    Codex には 16:9 を指示済みだが、ずれた場合の保険。テキストを切らないよう
    クロップではなくレターボックス（白背景パッド）でアスペクトを合わせる。
    既に概ね 16:9 ならそのままリサイズする。
    """
    from PIL import Image, ImageOps

    src = Image.open(BytesIO(image_bytes))
    src.load()
    if src.mode not in ("RGB", "RGBA"):
        src = src.convert("RGB")

    target_ratio = target_w / target_h
    src_ratio = src.width / src.height
    tolerance = 0.02  # 2%以内のズレはそのままリサイズ

    if abs(src_ratio - target_ratio) / target_ratio <= tolerance:
        out = src.resize((target_w, target_h), Image.LANCZOS)
    else:
        # アスペクトが大きく違う場合のみレターボックス（内容を欠けさせない）
        bg = (255, 255, 255) if src.mode == "RGB" else (255, 255, 255, 255)
        out = ImageOps.pad(src, (target_w, target_h), method=Image.LANCZOS, color=bg)

    buf = BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()


LOGO_POSITIONS = ("bottom-right", "bottom-left", "top-right", "top-left")


def _composite_logo(image_bytes: bytes, logo_path: str) -> bytes:
    """画像の指定コーナーにロゴを合成する。

    ブランドごとの調整は環境変数で行う（design-setup が preset config から設定する）:
      SLIDE_LOGO_POSITION: bottom-right(default) / bottom-left / top-right / top-left
      SLIDE_LOGO_SCALE:    画像幅に対するロゴ幅の比率（default 0.09）
    """
    from PIL import Image

    if not logo_path or not os.path.exists(logo_path):
        return image_bytes

    position = os.environ.get("SLIDE_LOGO_POSITION", "bottom-right")
    if position not in LOGO_POSITIONS:
        sys.stderr.write(
            f"[composite_logo] warning: unknown SLIDE_LOGO_POSITION={position}, "
            f"using bottom-right\n"
        )
        position = "bottom-right"
    try:
        scale = float(os.environ.get("SLIDE_LOGO_SCALE", "0.09"))
    except ValueError:
        scale = 0.09
    scale = min(max(scale, 0.02), 0.30)

    base = Image.open(BytesIO(image_bytes)).convert("RGBA")
    logo = Image.open(logo_path).convert("RGBA")

    target_w = max(1, int(base.width * scale))
    ratio = target_w / logo.width
    target_h = max(1, int(logo.height * ratio))
    logo = logo.resize((target_w, target_h), Image.LANCZOS)

    # 横は幅基準、下は高さ基準。16:9 では幅>高さなので同率でも幅基準だと下余白が
    # ピクセル換算で大きくなり、ロゴが下端から浮いて「高い位置」に見えてしまう。
    margin_x = int(base.width * 0.015)
    margin_y = int(base.height * 0.015)
    x = margin_x if position.endswith("left") else base.width - target_w - margin_x
    y = margin_y if position.startswith("top") else base.height - target_h - margin_y
    base.alpha_composite(logo, (x, y))

    buf = BytesIO()
    base.save(buf, format="PNG")
    return buf.getvalue()


class CodexImageProvider(ImageProvider):
    """Codex 内蔵 gpt-image-2 を使った画像生成プロバイダ（サブスク枠）。"""

    CAPABILITIES = Capability(
        name="codex",
        model=MODEL_ID,
        native_16_9=True,
        max_reference_images=4,           # codex exec -i で添付（ロゴのみローカル合成で確実に焼く）
        supports_grounding=False,
        supports_thinking=False,
        supports_transparent_bg=False,
        default_parallel=DEFAULT_PARALLEL,
        size_map=SIZE_MAP,
    )

    def generate(self, request: ImageRequest) -> ImageResponse:
        slide_name = os.path.basename(request.output_path)

        # 未対応フラグは警告（エラーにはしない）
        if request.grounding:
            print(f"⚠️  Codex: grounding非対応、スキップ: {slide_name}", file=sys.stderr)
        if request.thinking_level:
            print(f"⚠️  Codex: thinking_level非対応、スキップ: {slide_name}", file=sys.stderr)

        out_dir = os.path.dirname(request.output_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)

        # codex_app_server_client を import（scripts/ を import パスに追加）
        scripts_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from codex_app_server_client import generate_image  # type: ignore

        reference_images = []
        # v6: reference_images（複数・スタイルアンカー等）を先頭に、個別参照を後段に
        for path in list(request.reference_images) + [request.reference_image_path]:
            if not path:
                continue
            if os.path.exists(path):
                if path not in reference_images:
                    reference_images.append(path)
            else:
                print(f"⚠️  Codex: 参照画像が見つかりません（スキップ）: {path}",
                      file=sys.stderr)

        result = generate_image(
            prompt=request.prompt,
            output_path=request.output_path,
            image_size=request.image_size,
            aspect="16:9",
            backend="auto",
            max_retries=request.max_retries,
            retry_delay=request.retry_delay,
            reference_images=reference_images or None,
        )

        if not result.ok or not result.image_bytes:
            return ImageResponse(
                success=False,
                output_path=request.output_path,
                error=result.error or "Codex generation failed",
                attempts=result.attempts,
            )

        try:
            target_w, target_h = _target_dims(request.image_size)
            image_bytes = _normalize_to_16_9(result.image_bytes, target_w, target_h)

            # v6: 素の生成画像（ロゴ・フッター前）を保存。差分編集時の参照元になる
            if request.raw_dir:
                os.makedirs(request.raw_dir, exist_ok=True)
                raw_path = os.path.join(request.raw_dir, os.path.basename(request.output_path))
                with open(raw_path, "wb") as f:
                    f.write(image_bytes)

            if not request.skip_finish:
                # ロゴ合成（指定があれば。Codex への添付ではなくローカルで確実に焼く）
                logo_path = request.logo_path
                if logo_path and os.path.exists(logo_path):
                    image_bytes = _composite_logo(image_bytes, logo_path)

                # フッターウォーターマーク（既存ユーティリティを再利用）
                from footer_utils import apply_footer_to_bytes  # type: ignore
                image_bytes = apply_footer_to_bytes(image_bytes, output_format="PNG")

            with open(request.output_path, "wb") as f:
                f.write(image_bytes)
        except Exception as e:
            return ImageResponse(
                success=False,
                output_path=request.output_path,
                error=f"後処理エラー: {type(e).__name__}: {e}",
                attempts=result.attempts,
            )

        print(f"✅ 成功 (Codex/{result.backend}): {slide_name} ({len(image_bytes):,} bytes)",
              file=sys.stderr)
        return ImageResponse(
            success=True,
            output_path=request.output_path,
            metadata={"backend": result.backend, "codex_saved_path": result.saved_path},
            attempts=result.attempts,
        )
