"""Agent 基类：收敛公共链路，子类只实现差异部分。

这是整个项目的架构核心。四个业务 Agent（以及后续新增的任何一个）走的都是同一条链路：

    检索知识 → 组装 Prompt → 调用模型 → 结构化解析 → 异常兜底

基类把这条链路固化下来，子类只需要实现两件事：
    1. build_prompt()  —— 我是谁、我要模型干什么
    2. output_schema() —— 我要模型返回什么结构

结果就是：新增一个 Agent 的改动量从「复制整套流程」降为「两个方法」。
面试被问「为什么要有这一层抽象」时，标准答案是：
    避免每个 Agent 各写一遍检索和容错，导致某个 Agent 漏掉容错、输出脏数据
    把前端搞崩 —— 把易错的地方收敛到一处，是这类系统最重要的工程决策。
"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any

from ..core.embeddings import Embedder
from ..core.llm import LLMGateway, parse_json_lenient
from ..core.rerank import rerank
from ..core.vectorstore import RetrievedChunk, VectorStore
from ..errors import KnowledgeBaseError, ModelCallError

logger = logging.getLogger(__name__)


@dataclass
class AgentResult:
    """Agent 执行结果。结构固定，前端可以无差别渲染。"""

    agent: str
    domain: str
    data: dict[str, Any]
    knowledge: list[RetrievedChunk] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "domain": self.domain,
            "data": self.data,
            "knowledge": [
                {
                    "text": c.text[:200],
                    "source": c.source,
                    "domain": c.domain,
                    "score": c.score,
                }
                for c in self.knowledge
            ],
            "meta": self.meta,
        }


class BaseAgent(ABC):
    """业务 Agent 基类。"""

    #: 知识域，对应 data/docs 下的子目录名，检索时按此过滤
    domain: str = "general"
    #: Agent 标识
    name: str = "base"
    #: 一句话说明，用于前端展示和 /agents 接口
    description: str = ""

    # --- 子类必须实现 -------------------------------------------------
    @abstractmethod
    def build_prompt(self, payload: dict[str, Any], knowledge: str) -> tuple[str, str]:
        """返回 (system_prompt, user_prompt)。knowledge 是检索到并拼好的知识文本。"""

    @abstractmethod
    def output_schema(self) -> dict[str, Any]:
        """返回输出结构的 JSON Schema 描述，会被写进 Prompt 约束模型输出。"""

    # --- 可选覆写 -----------------------------------------------------
    def extra_user_context(self, payload: dict[str, Any]) -> str:
        """子类可覆写，追加业务专属的上下文。默认直接序列化 payload。"""
        lines = [f"- {k}: {v}" for k, v in payload.items() if v]
        return "\n".join(lines)

    def temperature(self) -> float:
        return 0.3

    # --- 公共链路（基类固化，子类不覆写）--------------------------------
    async def run(
        self,
        payload: dict[str, Any],
        *,
        gateway: LLMGateway,
        store: VectorStore,
        embedder: Embedder,
        top_k: int,
    ) -> AgentResult:
        started = time.perf_counter()

        # 1) 检索本域知识
        knowledge_chunks = await self._retrieve(payload, store, embedder, top_k)
        knowledge_text = self._format_knowledge(knowledge_chunks)

        # 2) 组装 Prompt（把知识和输出 Schema 一起注入）
        system, user = self.build_prompt(payload, knowledge_text)
        schema = self.output_schema()
        system = (
            f"{system}\n\n"
            f"【输出格式要求】\n"
            f"必须且只能输出一个 JSON 对象，不要输出任何解释文字或 Markdown 围栏。\n"
            f"JSON 结构如下：\n{schema}"
        )

        # 3) 调用模型
        raw, meta = await gateway.complete(
            system, user, json_mode=True, temperature=self.temperature()
        )

        # 4) 结构化解析（三级容错，失败直接抛 502，不透传脏数据）
        data = parse_json_lenient(raw)

        elapsed = round((time.perf_counter() - started) * 1000, 2)
        meta["elapsed_ms"] = elapsed
        meta["knowledge_hits"] = len(knowledge_chunks)
        meta["agent"] = self.name

        logger.info(
            "agent.run",
            extra={
                "agent": self.name,
                "domain": self.domain,
                "elapsed_ms": elapsed,
                "hits": len(knowledge_chunks),
            },
        )

        return AgentResult(
            agent=self.name,
            domain=self.domain,
            data=data,
            knowledge=knowledge_chunks,
            meta=meta,
        )

    # --- 流式链路（供 SSE 使用）----------------------------------------
    async def stream(
        self,
        payload: dict[str, Any],
        *,
        gateway: LLMGateway,
        store: VectorStore,
        embedder: Embedder,
        top_k: int,
    ):
        """流式执行。产出四类事件：meta → knowledge → delta* → done。

        meta 事件携带模型路由结果（走了轻量还是重量模型、路由得分），
        这是前端成本看板的数据来源，也是面试演示"省钱"这个卖点的关键。
        """
        started = time.perf_counter()

        knowledge_chunks = await self._retrieve(payload, store, embedder, top_k)
        knowledge_text = self._format_knowledge(knowledge_chunks)

        system, user = self.build_prompt(payload, knowledge_text)
        schema = self.output_schema()
        system = (
            f"{system}\n\n"
            f"【输出格式要求】\n必须且只能输出一个 JSON 对象，不要输出解释文字或 Markdown 围栏。\n"
            f"JSON 结构如下：\n{schema}"
        )

        # 路由决策在调用前算出并下发给前端，让"选了哪个模型"变成可见的。
        # 同时把 tier 回传给 gateway，避免它内部再算一遍复杂度 —— 一次决策，两处复用。
        task_text = f"{system}\n{user}"
        model, tier, score = gateway.resolve_route(task_text)
        yield {
            "type": "meta",
            "model": model,
            "tier": tier,
            "route_score": score,
            "hits": len(knowledge_chunks),
            "retrieve_ms": round((time.perf_counter() - started) * 1000, 2),
        }

        yield {"type": "knowledge", "items": [asdict(c) for c in knowledge_chunks]}

        started_gen = time.perf_counter()
        async for delta in gateway.stream(
            system, user, temperature=self.temperature(), force_tier=tier
        ):
            yield {"type": "delta", "text": delta}

        yield {
            "type": "done",
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            "generate_ms": round((time.perf_counter() - started_gen) * 1000, 2),
        }

    # --- 内部方法 ------------------------------------------------------
    async def _retrieve(
        self,
        payload: dict[str, Any],
        store: VectorStore,
        embedder: Embedder,
        top_k: int,
    ) -> list[RetrievedChunk]:
        query = self._build_query(payload)
        try:
            vectors = await embedder.embed([query])
        except ModelCallError as exc:
            # 向量化失败（如 Key 未配 / 网络问题）也降级为不检索，
            # 而不是让整个请求失败。Agent 仍能基于通用经验作答。
            logger.warning("agent.embed_failed", extra={"agent": self.name, "error": str(exc)})
            vectors = []
        except Exception as exc:
            # 检索是增强手段，不是主链路：任何未预期异常都只降级，绝不能拖垮整个请求
            logger.warning(
                "agent.embed_unexpected",
                extra={"agent": self.name, "error": str(exc)[:200]},
            )
            vectors = []
        embedding = vectors[0] if vectors else None
        try:
            # 召回 2 倍候选，交给 rerank 做词面二次重排后再取 top_k，
            # 缓解纯向量召回"字面命中却被排后"的噪声（见 core/rerank.py）。
            # query_text 用于无向量的本地降级场景，保证文本检索用的是真实问题。
            candidates = store.query(
                embedding,
                domain=self.domain,
                top_k=max(top_k * 2, top_k),
                query_text=query,
            )
        except KnowledgeBaseError as exc:
            # 知识库不可用时降级为不检索，而不是让整个请求失败
            logger.warning(
                "agent.retrieve_failed",
                extra={"agent": self.name, "error": str(exc)[:200]},
            )
            return []
        if not candidates:
            return []
        return rerank(query, candidates, top_k=top_k)

    def _build_query(self, payload: dict[str, Any]) -> str:
        """从输入里拼出检索语句。子类可覆写以挑更关键的字段。"""
        parts = [str(v) for v in payload.values() if v]
        return " ".join(parts)[:500]

    @staticmethod
    def _format_knowledge(chunks: list[RetrievedChunk]) -> str:
        if not chunks:
            return "（知识库暂无相关内容，请基于通用跨境电商经验作答）"
        blocks = []
        for idx, chunk in enumerate(chunks, 1):
            blocks.append(f"[{idx}] 来源：{chunk.source}（相关度 {chunk.score}）\n{chunk.text}")
        return "\n\n".join(blocks)
