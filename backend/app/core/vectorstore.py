"""向量库仓储层：封装 ChromaDB，对外只暴露检索语义。

设计要点：
- 仓储层只做数据存取，不含业务判断（"该检索哪个域"是服务层的事）。
- 元数据过滤是这里的核心能力：每个切片带 domain 元数据，检索时 where 过滤，
  使选品问题不会召回广告知识 —— 这是 RAG 落地最容易忽略、也最容易出效果的一点。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Sequence

import chromadb
from chromadb.config import Settings as ChromaSettings

from ..config import Settings
from ..errors import KnowledgeBaseError

logger = logging.getLogger(__name__)

COLLECTION_NAME = "ecom_kb"


@dataclass
class RetrievedChunk:
    text: str
    source: str
    domain: str
    score: float


class VectorStore:
    """ChromaDB 持久化向量库。"""

    def __init__(self, settings: Settings, persist_dir: str) -> None:
        self.settings = settings
        self._client = chromadb.PersistentClient(
            path=persist_dir,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self._collection = self._client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

    def count(self) -> int:
        return self._collection.count()

    def reset(self) -> None:
        """清空并重建集合（用于知识库重建）。"""
        try:
            self._client.delete_collection(COLLECTION_NAME)
        except Exception:  # 集合不存在时忽略
            pass
        self._collection = self._client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

    def _matched_count(self, where: dict | None) -> int:
        """统计满足 where 条件的切片数（不取回内容，开销很小）。"""
        got = self._collection.get(where=where, include=[])
        ids = got.get("ids") or []
        return len(ids)

    def add(
        self,
        ids: Sequence[str],
        texts: Sequence[str],
        metadatas: Sequence[dict],
        embeddings: Sequence[Sequence[float]] | None = None,
    ) -> None:
        """写入切片。embeddings 为 None 时由 ChromaDB 用内置模型生成。"""
        kwargs: dict = {
            "ids": list(ids),
            "documents": list(texts),
            "metadatas": list(metadatas),
        }
        if embeddings:
            kwargs["embeddings"] = [list(e) for e in embeddings]
        try:
            self._collection.add(**kwargs)
        except Exception as exc:
            raise KnowledgeBaseError(f"写入向量库失败: {exc}") from exc

    def query(
        self,
        query_embedding: Sequence[float] | None,
        *,
        domain: str,
        top_k: int,
        query_text: str | None = None,
    ) -> list[RetrievedChunk]:
        """按域过滤检索。domain="*" 表示不限制域（跨域检索）。

        query_embedding 为 None 时（本地/无 embedding 场景）退回文本检索，
        此时必须用真实 query_text —— 之前这里写死传空串，等于用空文本去检索，
        返回的是库里排在最前面的任意切片，所谓"降级可用"实际是"降级即失效"。
        """
        if self.count() == 0:
            raise KnowledgeBaseError(
                "知识库为空，请先调用 POST /api/kb/rebuild 或 python scripts/ingest.py 建立索引"
            )

        where = None if domain in ("*", "", "all") else {"domain": domain}
        kwargs: dict = {
            "n_results": max(1, min(top_k, self.count())),
            "where": where,
        }
        if query_embedding is not None:
            kwargs["query_embeddings"] = [list(query_embedding)]
        else:
            kwargs["query_texts"] = [query_text or ""]

        try:
            result = self._collection.query(**kwargs)
        except Exception as exc:
            # 部分 ChromaDB 版本在「域内切片数 < n_results」时直接报错。
            # 这时不是检索失败，只是该域知识较少 —— 按域内实际数量重试一次。
            try:
                matched = self._matched_count(where)
            except Exception:  # 连计数都失败，说明是真故障
                raise KnowledgeBaseError(f"检索失败: {exc}") from exc
            if matched <= 0:
                return []
            kwargs["n_results"] = matched
            try:
                result = self._collection.query(**kwargs)
            except Exception as exc2:
                raise KnowledgeBaseError(f"检索失败: {exc2}") from exc2

        documents = result.get("documents") or [[]]
        metadatas = result.get("metadatas") or [[]]
        distances = result.get("distances") or [[]]

        chunks: list[RetrievedChunk] = []
        for doc, meta, dist in zip(documents[0], metadatas[0], distances[0]):
            meta = meta or {}
            chunks.append(
                RetrievedChunk(
                    text=doc,
                    source=meta.get("source", "unknown"),
                    domain=meta.get("domain", "unknown"),
                    # ChromaDB 返回的是余弦距离，转成相似度更直观
                    score=round(1 - float(dist), 4),
                )
            )
        return chunks
