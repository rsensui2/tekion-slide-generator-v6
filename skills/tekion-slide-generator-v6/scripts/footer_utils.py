#!/usr/bin/env python3
"""
フッターウォーターマーク焼き込みユーティリティ

PIL.ImageDraw で画像中央下に固定文字列 "TEKION Slide Generator v6" を焼き込む。
画像生成パイプライン (generate_slide_with_retry.py / providers/openai.py) で
PNG 保存前に各スライド画像へ適用され、以降の全ての配布物（PNG/PPTX/PDF）に
ブランド透かしが乗る。

設計: project/docs/skill/footer-watermark-spec.md
"""
import os
import sys
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont


# 環境変数 SLIDE_FOOTER_TEXT で上書き可能（未設定時は従来どおり TEKION 透かし）。
# 空文字（SLIDE_FOOTER_TEXT="") を渡すとフッターを焼き込まない。
FOOTER_TEXT = os.environ.get("SLIDE_FOOTER_TEXT", "TEKION Slide Generator v6")
FOOTER_COLOR = (156, 163, 175, 255)  # Gray-400 / RGBA
FOOTER_FONT_PATH = "/System/Library/Fonts/Helvetica.ttc"
FOOTER_FONT_SIZE_RATIO = 0.015        # 画像高さに対する比率
FOOTER_BOTTOM_MARGIN_RATIO = 0.012    # 画像高さに対する下端余白比率（中央下ギリギリ）

# 小さい画像 (例: --image-size 512px → height ≈ 512) では ratio から算出した
# font_size が 7px などになり、Helvetica.ttc 等の OpenType フォントでは
# `textbbox()` が `OSError: division by zero` で落ちる。最低 10px をかぶせて防ぐ。
FOOTER_FONT_SIZE_MIN = 10

_default_font_warned = False


def _load_font(font_path: str, font_size: int) -> ImageFont.ImageFont:
    """指定パスから TTF を読み込み、失敗時は PIL デフォルトフォントへフォールバック。

    フォールバック時も `font_size` を可能な限り反映する：Pillow 10.1+ は
    `ImageFont.load_default(size)` をサポートし、内部の DejaVuSans を
    指定サイズで返してくれる。これがないと 4K スライドでも 10px の極小
    ウォーターマークになり、macOS 以外の環境で実質的に見えなくなる。
    """
    global _default_font_warned
    try:
        return ImageFont.truetype(font_path, font_size)
    except (OSError, IOError):
        if not _default_font_warned:
            sys.stderr.write(
                f"[footer_utils] warning: font not found at {font_path}, "
                f"using PIL default font (DejaVuSans) at {font_size}px\n"
            )
            _default_font_warned = True
        try:
            return ImageFont.load_default(font_size)
        except TypeError:
            # Pillow < 10.1: load_default は引数を取らない (固定 10px)
            return ImageFont.load_default()


def apply_footer(
    img: Image.Image,
    text: str = FOOTER_TEXT,
    color: tuple = FOOTER_COLOR,
    font_path: str = FOOTER_FONT_PATH,
) -> Image.Image:
    """画像のコピーに左下フッターを焼き込んで返す。元画像は変更しない。

    Args:
        img: 入力 PIL Image
        text: 焼き込む文字列（デフォルト: FOOTER_TEXT）
        color: RGBA タプル
        font_path: TTF/TTC フォントパス（見つからない場合は default fallback）

    Returns:
        フッター焼き込み済みの PIL Image（元画像とは別オブジェクト）
    """
    out = img.copy()

    # 空文字なら透かしを焼き込まずそのまま返す（第三者向け資料などロゴ/透かし不要時）。
    if text is None or str(text).strip() == "":
        return out

    if out.mode not in ("RGB", "RGBA"):
        out = out.convert("RGBA")

    draw = ImageDraw.Draw(out)

    font_size = max(FOOTER_FONT_SIZE_MIN, int(out.height * FOOTER_FONT_SIZE_RATIO))
    font = _load_font(font_path, font_size)

    # 何らかの理由で textbbox がフォント計測失敗（小サイズフォント等）で落ちたら、
    # PIL の default フォントへフォールバックして再計測する。
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
    except OSError:
        font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    margin_y = int(out.height * FOOTER_BOTTOM_MARGIN_RATIO)
    x = (out.width - text_w) // 2
    y = out.height - margin_y - text_h

    fill = color if out.mode == "RGBA" else color[:3]
    draw.text((x, y), text, font=font, fill=fill)

    return out


def apply_footer_to_bytes(
    image_bytes: bytes,
    output_format: str = "PNG",
    text: str = FOOTER_TEXT,
) -> bytes:
    """画像バイト列にフッターを焼き込んで新しいバイト列を返す。

    画像生成 API が返した raw bytes をそのまま受け取り、
    フッター適用後のバイト列を返すラッパー。

    Args:
        image_bytes: 入力画像のバイト列（PNG/JPEG 等、PIL が開ける形式）
        output_format: 出力フォーマット（デフォルト: PNG）
        text: 焼き込む文字列

    Returns:
        フッター焼き込み済みのバイト列
    """
    src = Image.open(BytesIO(image_bytes))
    src.load()
    out = apply_footer(src, text=text)

    if output_format.upper() in ("JPEG", "JPG") and out.mode == "RGBA":
        out = out.convert("RGB")

    buf = BytesIO()
    out.save(buf, format=output_format)
    return buf.getvalue()
