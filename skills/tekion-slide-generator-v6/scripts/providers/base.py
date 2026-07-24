"""Provider抽象基底クラスとデータ型。

ImageProvider: 各プロバイダが実装するABC
ImageRequest: プロバイダに渡す統一リクエスト
ImageResponse: プロバイダが返す統一レスポンス
Capability: プロバイダの対応機能の宣言
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ImageRequest:
    """画像生成リクエスト（プロバイダ非依存）。"""
    prompt: str
    output_path: str
    api_key: str

    # 共通オプション
    image_size: str = "2K"  # 共通ラベル: 512px / 1K / 2K / 4K
    max_retries: int = 3
    retry_delay: float = 2.0

    # 参考画像（ロゴ等）
    logo_path: Optional[str] = None
    reference_image_path: Optional[str] = None
    reference_images: list[str] = field(default_factory=list)

    # Gemini固有（未対応プロバイダでは警告してスキップ）
    thinking_level: Optional[str] = None  # "minimal" | "High"
    grounding: bool = False

    # OpenAI固有
    quality: str = "medium"        # auto | low | medium | high
    input_fidelity: str = "high"   # low | high
    background: str = "auto"       # auto | transparent | opaque

    # v6: 差分編集サポート
    # raw_dir: ロゴ・フッター焼き込み前の「素の生成画像」を保存するディレクトリ。
    #   後日の差分編集はこの raw を参照画像にすることで、ロゴ・フッターの
    #   二重焼き込みを避けつつ決定的に再合成できる。
    raw_dir: Optional[str] = None
    # skip_finish: ロゴ合成・フッター焼き込みをスキップ（16:9正規化のみ行う）。
    #   仕上げ済み画像を参照にした編集で、二重焼き込みを避けるために使う。
    skip_finish: bool = False


@dataclass
class ImageResponse:
    """画像生成レスポンス（プロバイダ非依存）。"""
    success: bool
    output_path: str
    error: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    attempts: int = 1


@dataclass
class Capability:
    """プロバイダの対応機能宣言。"""
    name: str
    model: str
    native_16_9: bool
    max_reference_images: int            # 0=非対応
    supports_grounding: bool
    supports_thinking: bool
    supports_transparent_bg: bool
    default_parallel: int                # Tier/レート制限を考慮したデフォルト並列数
    size_map: dict[str, str] = field(default_factory=dict)  # 共通ラベル → provider固有サイズ

    def resolve_size(self, label: str) -> str:
        """共通サイズラベルをプロバイダ固有の値に解決する。"""
        return self.size_map.get(label, label)


class ImageProvider(ABC):
    """画像生成プロバイダの抽象基底クラス。"""

    CAPABILITIES: Capability

    @abstractmethod
    def generate(self, request: ImageRequest) -> ImageResponse:
        """1枚の画像を生成する（リトライ含む）。"""
        raise NotImplementedError

    def collect_reference_images(self, request: ImageRequest) -> list[str]:
        """リクエストから有効な参考画像パスを集める（ロゴ+参考画像+複数参考画像）。"""
        import os
        paths: list[str] = []
        if request.logo_path and os.path.exists(request.logo_path):
            paths.append(request.logo_path)
        if request.reference_image_path and os.path.exists(request.reference_image_path):
            paths.append(request.reference_image_path)
        for p in request.reference_images:
            if p and os.path.exists(p) and p not in paths:
                paths.append(p)
        return paths
