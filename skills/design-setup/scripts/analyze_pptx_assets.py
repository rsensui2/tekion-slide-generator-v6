#!/usr/bin/env python3
"""PPTX（スライドマスター/既存デッキ）からブランド素材を抽出する。

オンボーディングで「スライドマスターの PowerPoint を送ってくれ」と受け取ったファイルを解析し、
プリセット作成の材料を JSON で返す:

  - テーマカラー（ppt/theme/theme1.xml の dk1/lt1/accent1-6）
  - テーマフォント（majorFont / minorFont）
  - 埋め込み画像の一覧（ロゴ候補: 小さめ・横長・PNG を優先してスコアリング）

画像は --extract-dir に書き出す。ロゴの最終判断は Claude が Read で目視して行う。

使い方:
    python3 analyze_pptx_assets.py --file deck.pptx --extract-dir /tmp/out [--json]
"""
import argparse
import json
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree

DRAWINGML_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".emf", ".wmf")


def extract_theme(zf: zipfile.ZipFile) -> dict:
    """テーマXMLから配色とフォントを取り出す。"""
    theme_names = sorted(n for n in zf.namelist() if re.match(r"ppt/theme/theme\d+\.xml$", n))
    if not theme_names:
        return {"colors": {}, "fonts": {}}

    root = ElementTree.fromstring(zf.read(theme_names[0]))
    colors = {}
    scheme = root.find(f".//{DRAWINGML_NS}clrScheme")
    if scheme is not None:
        for slot in scheme:
            tag = slot.tag.replace(DRAWINGML_NS, "")
            srgb = slot.find(f"{DRAWINGML_NS}srgbClr")
            sys_clr = slot.find(f"{DRAWINGML_NS}sysClr")
            if srgb is not None:
                colors[tag] = f"#{srgb.get('val', '').upper()}"
            elif sys_clr is not None:
                last = sys_clr.get("lastClr")
                colors[tag] = f"#{last.upper()}" if last else sys_clr.get("val", "")

    fonts = {}
    for kind in ("majorFont", "minorFont"):
        node = root.find(f".//{DRAWINGML_NS}{kind}")
        if node is not None:
            latin = node.find(f"{DRAWINGML_NS}latin")
            ea = node.find(f"{DRAWINGML_NS}ea")
            fonts[kind] = {
                "latin": latin.get("typeface") if latin is not None else None,
                "east_asian": ea.get("typeface") if ea is not None else None,
            }
    return {"colors": colors, "fonts": fonts, "theme_file": theme_names[0]}


def logo_score(name: str, width: int, height: int, size_bytes: int) -> float:
    """ロゴらしさの粗いスコア（高いほど候補）。最終判断は目視。"""
    score = 0.0
    lower = Path(name).name.lower()
    if "logo" in lower:
        score += 100
    if lower.endswith(".png"):
        score += 20  # 透過の可能性
    if width and height:
        aspect = width / height
        if 1.5 <= aspect <= 8:
            score += 30  # 横長ワードマーク
        if width < 1200 and height < 500:
            score += 20  # 写真より小さい
        if width * height > 2_000_000:
            score -= 40  # 背景写真らしい
    if size_bytes < 200_000:
        score += 10
    return score


def extract_images(zf: zipfile.ZipFile, extract_dir: Path) -> list:
    """media 内の画像を書き出し、寸法付きで一覧化する。"""
    try:
        from PIL import Image
    except ImportError:
        Image = None

    results = []
    media = [n for n in zf.namelist()
             if n.startswith("ppt/media/") and n.lower().endswith(IMAGE_EXTS)]
    extract_dir.mkdir(parents=True, exist_ok=True)
    for name in media:
        data = zf.read(name)
        out_path = extract_dir / Path(name).name
        out_path.write_bytes(data)
        width = height = None
        if Image is not None and out_path.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif"):
            try:
                with Image.open(out_path) as img:
                    width, height = img.size
            except OSError:
                pass
        results.append({
            "file": str(out_path),
            "width": width,
            "height": height,
            "bytes": len(data),
            "logo_score": logo_score(name, width or 0, height or 0, len(data)),
        })
    return sorted(results, key=lambda r: -r["logo_score"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, help="解析する .pptx / .potx")
    parser.add_argument("--extract-dir", required=True, help="画像の書き出し先ディレクトリ")
    parser.add_argument("--json", action="store_true", help="JSON のみ出力（デフォルトでも JSON）")
    args = parser.parse_args()

    pptx_path = Path(args.file).expanduser()
    if not pptx_path.is_file():
        print(json.dumps({"error": f"ファイルが見つかりません: {pptx_path}"}, ensure_ascii=False))
        sys.exit(1)

    try:
        with zipfile.ZipFile(pptx_path) as zf:
            theme = extract_theme(zf)
            images = extract_images(zf, Path(args.extract_dir).expanduser())
    except zipfile.BadZipFile:
        print(json.dumps({"error": "PPTX として読めません（zip 展開失敗）"}, ensure_ascii=False))
        sys.exit(1)

    print(json.dumps({
        "source": str(pptx_path),
        "theme": theme,
        "images": images,
        "logo_candidates": [r["file"] for r in images[:3]],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
