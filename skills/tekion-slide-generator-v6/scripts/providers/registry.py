"""Provider レジストリ。プロバイダ名から実装クラスを解決する。

OpenAI（gpt-image-2 / API 従量課金）と Codex（gpt-image-2 / サブスク枠）を
Provider 経由で扱う。Gemini は既存の generate_slide_with_retry.py 内の実装を使う。
"""
from __future__ import annotations

from .base import ImageProvider
from .codex import CodexImageProvider
from .mock import MockImageProvider
from .openai import OpenAIImageProvider


_PROVIDERS: dict[str, type[ImageProvider]] = {
    "openai": OpenAIImageProvider,
    "codex": CodexImageProvider,
    "mock": MockImageProvider,
}


def get_provider(name: str) -> ImageProvider:
    """プロバイダ名からインスタンスを取得する。

    Raises:
        ValueError: 未知のプロバイダ名
    """
    key = name.lower().strip()
    if key not in _PROVIDERS:
        available = ", ".join(sorted(_PROVIDERS.keys()))
        raise ValueError(f"Unknown provider: {name!r}. Available: {available}")
    return _PROVIDERS[key]()


def list_providers() -> list[str]:
    """登録済みプロバイダ名の一覧を返す。"""
    return sorted(_PROVIDERS.keys())
