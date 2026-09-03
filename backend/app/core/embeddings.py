"""向量化：优先用厂商 embedding 接口，不可用时降级到 ChromaDB 本地模型。

降级路径（面试可以主动讲，说明考虑过兜底）：
- DeepSeek 不提供 embedding 接口，此时降级到 ChromaDB 内置的 ONNX 本地模型。
- 本地模型仍然是真实语义向量（不是占位哈希），只是维度和效果弱于云端。
"""
from __future__ import annotations

import logging
from typing import Sequence

from openai import AsyncOpenAI

from ..config import Settings
from ..errors import ModelCallError

logger = logging.getLogger(__name__)


class Embedder:
    """文本向量化。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: AsyncOpenAI | None = None
        if settings.supports_embedding and settings.model_embedding:
            self._client = AsyncOpenAI(
                api_key=settings.api_key or "ollama",
                base_url=settings.api_base,
                timeout=settings.request_timeout,
                max_retries=1,
            )
        elif settings.provider == "ollama" and settings.model_embedding:
            self._client = AsyncOpenAI(
                api_key="ollama",
                base_url=settings.api_base,
                timeout=settings.request_timeout,
            )

    @property
    def mode(self) -> str:
        if self._client is not None:
            return f"remote:{self.settings.model_embedding}"
        return "local:chroma-default"

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """把一批文本转成向量。返回 None 表示交给 ChromaDB 用内置模型。"""
        if self._client is None:
            # 返回空列表，调用方（vectorstore）会把 embedding 置为 None，
            # 由 ChromaDB 用内置 ONNX 模型生成。
            logger.info("embed.local_fallback", extra={"count": len(texts)})
            return []

        cleaned = [t.replace("\n", " ").strip() for t in texts]
        try:
            resp = await self._client.embeddings.create(
                model=self.settings.model_embedding,
                input=cleaned,
            )
        except Exception as exc:
            raise ModelCallError(f"向量化失败（{self.settings.provider_label}）: {exc}") from exc

        vectors = [list(item.embedding) for item in resp.data]
        logger.info(
            "embed.remote",
            extra={"count": len(vectors), "dim": len(vectors[0]) if vectors else 0},
        )
        return vectors
