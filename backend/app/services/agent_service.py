"""业务服务层。

职责边界：
- 服务层承载业务编排（该用哪个 Agent、要不要重建库），不依赖 HTTP 请求/响应对象。
- 控制器只做参数解析和结果格式化，业务判断一律在这里。
- 这样分层后，服务层可以被脚本、定时任务、测试直接调用，不必起 HTTP 服务。
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, AsyncIterator

from ..agents import create_agent, list_agents
from ..agents.base import BaseAgent
from ..config import Settings
from ..core.embeddings import Embedder
from ..core.llm import LLMGateway
from ..core.rag import chunk_document, load_documents
from ..core.vectorstore import VectorStore
from ..errors import ConfigError, KnowledgeBaseError, NotFoundError

logger = logging.getLogger(__name__)

# 知识库源文档目录：backend/data/docs
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DOCS_DIR = os.path.join(BACKEND_DIR, "data", "docs")

# 每批向量化的文本数。
# 阿里云百炼 text-embedding-v3 单次最多 10 条，超过会报
# "batch size is invalid, it should not be larger than 10"。
# 取各厂商公共下限，换 provider 不用改代码。
BATCH_SIZE = 10


class AgentService:
    """Agent 编排服务。"""

    def __init__(
        self,
        *,
        gateway: LLMGateway,
        store: VectorStore,
        embedder: Embedder,
        top_k: int,
    ) -> None:
        self.gateway = gateway
        self.store = store
        self.embedder = embedder
        self.top_k = top_k

    def available_agents(self) -> list[dict[str, str]]:
        return list_agents()

    def output_schema(self, agent_name: str) -> dict[str, Any]:
        """取该 Agent 的输出结构定义，供批量任务做规则校验。

        单独开一个方法而不是让调用方直接 `from ..agents import create_agent`：
        批量任务层只依赖服务层，不该知道 Agent 是怎么注册和创建的 ——
        否则"新增 Agent 只需改注册表"这个约定就被绕过去了。
        """
        return self._get_agent(agent_name).output_schema()

    def _get_agent(self, agent_name: str) -> BaseAgent:
        """按名字取 Agent，未知名字转 404。

        之前 run() 和 stream() 各自写了一遍同样的 try/except KeyError，
        两处文案一旦改一处漏一处，就会给出不一致的报错。
        """
        try:
            return create_agent(agent_name)
        except KeyError:
            available = ", ".join(a["name"] for a in list_agents())
            raise NotFoundError(f"未知 Agent: {agent_name}，可用：{available}") from None

    async def run(self, agent_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        """执行指定 Agent，返回可序列化的结果。"""
        agent = self._get_agent(agent_name)
        result = await agent.run(
            payload,
            gateway=self.gateway,
            store=self.store,
            embedder=self.embedder,
            top_k=self.top_k,
        )
        return result.to_dict()

    async def stream(
        self, agent_name: str, payload: dict[str, Any]
    ) -> AsyncIterator[dict[str, Any]]:
        """流式执行指定 Agent，产出 SSE 事件字典。"""
        agent = self._get_agent(agent_name)
        async for event in agent.stream(
            payload,
            gateway=self.gateway,
            store=self.store,
            embedder=self.embedder,
            top_k=self.top_k,
        ):
            yield event


class KnowledgeService:
    """知识库管理服务：统计 + 重建索引。

    重建逻辑放在服务层而不是脚本里，是为了让 HTTP 接口和命令行共用同一份实现 ——
    之前脚本被 API 反向 import，导致跨包导入失败，这是分层没做干净的后果。
    """

    def __init__(
        self,
        store: VectorStore,
        embedder: Embedder,
        settings: Settings | None = None,
        docs_dir: str = DOCS_DIR,
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.settings = settings
        self.docs_dir = docs_dir

    def stats(self) -> dict[str, Any]:
        return {
            "chunks": self.store.count(),
            "embedding_mode": self.embedder.mode,
        }

    async def rebuild(self) -> dict[str, Any]:
        """扫描 docs_dir → 切分 → 分批向量化 → 重建向量索引。

        全流程在一个方法里，HTTP 与 CLI 两种入口行为完全一致。

        错误一律用类型化异常：之前这里抛 RuntimeError，会被全局兜底成
        500「服务内部错误」。但"没找到文档""ID 重复"都是使用者能自己修的问题，
        报 500 既误导排查方向，也让调用方无法按 code 做分支处理。
        """
        if self.settings is None:
            raise ConfigError("KnowledgeService 缺少 settings，无法重建知识库")

        started = time.perf_counter()
        documents = load_documents(self.docs_dir)
        if not documents:
            raise KnowledgeBaseError(f"未在 {self.docs_dir} 下找到任何 .md 文档")

        all_chunks = []
        for doc_ordinal, (domain, filename, content) in enumerate(documents):
            chunks = chunk_document(
                content,
                source=filename,
                domain=domain,
                chunk_size=self.settings.chunk_size,
                overlap=self.settings.chunk_overlap,
                # 传入全局序号，保证 chunk_id 不跨文档重复
                doc_ordinal=doc_ordinal,
            )
            all_chunks.extend(chunks)

        # 写入前自检：ID 必须唯一，否则 ChromaDB 会拒绝整批。
        # ids_all.count(i) 在循环里是 O(n^2)，21 篇文档量级无感；
        # 文档量上千时应改成 Counter，这里保持直白。
        ids_all = [c.chunk_id for c in all_chunks]
        if len(set(ids_all)) != len(ids_all):
            dupes = sorted({i for i in ids_all if ids_all.count(i) > 1})[:5]
            raise KnowledgeBaseError(f"切片 ID 重复，写入会失败：{dupes}")

        self.store.reset()

        total = len(all_chunks)
        for start in range(0, total, BATCH_SIZE):
            batch = all_chunks[start : start + BATCH_SIZE]
            texts = [c.text for c in batch]
            vectors = await self.embedder.embed(texts)
            self.store.add(
                ids=[c.chunk_id for c in batch],
                texts=texts,
                metadatas=[
                    {"source": c.source, "domain": c.domain, "index": c.index}
                    for c in batch
                ],
                embeddings=vectors if vectors else None,
            )
            done = min(start + BATCH_SIZE, total)
            logger.info("kb.rebuild.progress", extra={"done": done, "total": total})

        elapsed = round((time.perf_counter() - started) * 1000, 2)
        logger.info("kb.rebuild.done", extra={"chunks": total, "elapsed_ms": elapsed})
        return {
            "chunks": total,
            "files": len(documents),
            "embedding_mode": self.embedder.mode,
            "elapsed_ms": elapsed,
        }
