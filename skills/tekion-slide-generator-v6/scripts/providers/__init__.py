"""TEKION Slide Generator v5 - Image generation providers.

Provider抽象レイヤ。Gemini / OpenAI など複数の画像生成プロバイダを統一APIで扱う。
"""
from .base import ImageProvider, ImageRequest, ImageResponse, Capability
from .registry import get_provider, list_providers

__all__ = [
    "ImageProvider",
    "ImageRequest",
    "ImageResponse",
    "Capability",
    "get_provider",
    "list_providers",
]
