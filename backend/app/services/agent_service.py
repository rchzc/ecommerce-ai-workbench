"""业务服务层。

职责边界：
- 服务层承载业务编排（该用哪个 Agent、要不要重建库），不依赖 HTTP 请求/响应对象。
- 控制器只做参数解析和结果格式化，业务判断一律在这里。
- 这样分层后，服务层可以被脚本、定时任务、测试直接调用，不必起 HTTP 服务。
"""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator

from ..agents import create_agent, list_agents
from ..core.embeddings import Embedder
from ..core.llm import LLMGateway
from ..core.vectorstore import VectorStore
from ..errors import NotFoundError

logger = logging.getLogger(__name__)


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

    async def run(self, agent_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        """执行指定 Agent，返回可序列化的结果。"""
        try:
            agent = create_agent(agent_name)
        except KeyError:
            available = ", ".join(a["name"] for a in list_agents())
            raise NotFoundError(f"未知 Agent: {agent_name}，可用：{available}") from None

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
        try:
            agent = create_agent(agent_name)
        except KeyError:
            available = ", ".join(a["name"] for a in list_agents())
            raise NotFoundError(f"未知 Agent: {agent_name}，可用：{available}") from None

        async for event in agent.stream(
            payload,
            gateway=self.gateway,
            store=self.store,
            embedder=self.embedder,
            top_k=self.top_k,
        ):
            yield event


class KnowledgeService:
    """知识库管理服务。"""

    def __init__(self, store: VectorStore, embedder: Embedder) -> None:
        self.store = store
        self.embedder = embedder

    def stats(self) -> dict[str, Any]:
        return {
            "chunks": self.store.count(),
            "embedding_mode": self.embedder.mode,
        }

    def usage_snapshot(self, gateway: LLMGateway) -> dict[str, Any]:
        return gateway.usage.snapshot()
