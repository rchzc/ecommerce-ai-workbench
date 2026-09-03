"""HTTP 控制器。

严格约束：这一层不写业务逻辑。
只做三件事：解析请求 → 调用服务 → 格式化响应。
所有业务判断都在 services/ 和 agents/ 里，所有数据访问都在 core/ 里。
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends

from ..agents import list_agents
from ..core.embeddings import Embedder
from ..core.llm import LLMGateway
from ..core.vectorstore import VectorStore
from ..errors import ValidationError
from ..schemas import (
    AgentRequest,
    HealthResponse,
    KbStatsResponse,
    RebuildResponse,
)
from ..services.agent_service import AgentService, KnowledgeService
from . import deps

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health", response_model=HealthResponse, summary="健康检查")
async def health(
    gateway: LLMGateway = Depends(deps.get_gateway),
    store: VectorStore = Depends(deps.get_store),
    embedder: Embedder = Depends(deps.get_embedder),
) -> HealthResponse:
    """健康检查端点。用于 Docker healthcheck 和前端启动自检。"""
    return HealthResponse(
        status="ok",
        provider=gateway.settings.provider,
        provider_label=gateway.settings.provider_label,
        model_light=gateway.settings.model_light,
        model_heavy=gateway.settings.model_heavy,
        embedding_mode=embedder.mode,
        knowledge_chunks=store.count(),
        agents=[a["name"] for a in list_agents()],
    )


@router.get("/agents", summary="列出所有可用 Agent")
async def agents() -> dict:
    """前端据此动态渲染面板，新增 Agent 不需要改前端。"""
    return {"items": list_agents()}


@router.post("/agent/{name}", summary="执行指定 Agent")
async def run_agent(
    name: str,
    body: AgentRequest,
    service: AgentService = Depends(deps.get_agent_service),
) -> dict:
    return await service.run(name, body.payload)


@router.get("/kb/stats", response_model=KbStatsResponse, summary="知识库统计")
async def kb_stats(
    service: KnowledgeService = Depends(deps.get_knowledge_service),
) -> KbStatsResponse:
    stats = service.stats()
    return KbStatsResponse(**stats)


@router.post("/kb/rebuild", response_model=RebuildResponse, summary="重建知识库")
async def kb_rebuild(
    store: VectorStore = Depends(deps.get_store),
    embedder: Embedder = Depends(deps.get_embedder),
    gateway: LLMGateway = Depends(deps.get_gateway),
) -> RebuildResponse:
    """重新扫描 data/docs 并重建向量索引。

    走 HTTP 而不是脚本，是为了让运维（和你自己）无需登录服务器就能重建。
    """
    from ..scripts.ingest import rebuild_knowledge_base

    result = await rebuild_knowledge_base(
        settings=gateway.settings, store=store, embedder=embedder
    )
    return RebuildResponse(**result)


@router.post("/agent/{name}/stream", summary="流式执行指定 Agent")
async def stream_agent(
    name: str,
    body: AgentRequest,
    service: AgentService = Depends(deps.get_agent_service),
):
    """SSE 流式接口：先返回命中的知识，再逐块返回模型输出。

    前端据此实现打字机效果。长任务（如选品分析）不用干等十几秒。
    """
    from fastapi.responses import StreamingResponse

    from ..errors import AppError

    async def event_gen():
        try:
            async for event in service.stream(name, body.payload):
                yield _sse(event)
        except AppError as exc:
            logger.warning("stream.failed", extra={"agent": name, "code": exc.code})
            yield _sse({"type": "error", **exc.to_dict()["error"]})
        except Exception as exc:  # 兜底，避免 SSE 流中断在半路
            logger.exception("stream.crashed", extra={"agent": name})
            yield _sse({"type": "error", "code": "internal_error", "message": str(exc)})

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/usage", summary="模型用量统计")
async def usage(
    gateway: LLMGateway = Depends(deps.get_gateway),
) -> dict:
    """返回累计 token 用量。前端成本看板用。"""
    return gateway.usage.snapshot()


@router.get("/config/check", summary="配置自检")
async def config_check(
    gateway: LLMGateway = Depends(deps.get_gateway),
) -> dict:
    """暴露当前生效的配置（不含密钥），排查问题时很有用。"""
    s = gateway.settings
    return {
        "provider": s.provider,
        "provider_label": s.provider_label,
        "api_base": s.api_base,
        "model_light": s.model_light,
        "model_heavy": s.model_heavy,
        "model_embedding": s.model_embedding or "(本地降级)",
        "top_k": s.top_k,
        "chunk_size": s.chunk_size,
        "chunk_overlap": s.chunk_overlap,
    }


def _sse(event: dict) -> str:
    """把事件字典序列化为 SSE 帧。"""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
