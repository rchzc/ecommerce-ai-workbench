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
from ..schemas import (
    AgentRequest,
    HealthResponse,
    KbStatsResponse,
    RebuildResponse,
)
from ..services.agent_service import AgentService, KnowledgeService
from ..services.report_service import ReportService
from . import deps

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health", response_model=HealthResponse, summary="健康检查")
async def health(
    gateway: LLMGateway = Depends(deps.get_gateway),
    store: VectorStore = Depends(deps.get_store),
    embedder: Embedder = Depends(deps.get_embedder),
    service: AgentService = Depends(deps.get_agent_service),
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
        # 走服务层而不是直接 import agents 模块：控制器只认服务层，
        # 否则"控制器 → 服务 → 领域"的单向依赖就悄悄多出一条捷径，
        # 以后给列表加权限过滤时很容易漏掉这里。
        agents=[a["name"] for a in service.available_agents()],
    )


@router.get("/agents", summary="列出所有可用 Agent")
async def agents() -> dict:
    """前端据此动态渲染面板，新增 Agent 不需要改前端。

    这里刻意直连注册表、不注入 AgentService：Agent 清单是纯静态元数据，
    不依赖任何运行期组件，因此**服务未就绪时也必须可用** ——
    前端要先拿到清单才能渲染出面板，进而把配置错误提示显示在正确位置。
    若走依赖注入，配置一错这个接口也 503，前端就只剩一片空白。
    """
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
    service: KnowledgeService = Depends(deps.get_knowledge_service),
) -> RebuildResponse:
    """重新扫描 data/docs 并重建向量索引。

    走 HTTP 而不是脚本，是为了让运维（和你自己）无需登录服务器就能重建。
    重建逻辑在服务层，与命令行入口 scripts/ingest.py 共用同一份实现。
    """
    result = await service.rebuild()
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


# ---------------------------------------------------------------------------
# 销售日报与数据看板
# ---------------------------------------------------------------------------

@router.get("/report/dates", summary="日报可用日期列表")
async def report_dates(
    service: ReportService = Depends(deps.get_report_service),
) -> dict:
    """看板日期选择器的数据源：先拿日期范围再按日取详情。"""
    dates = service.available_dates()
    return {"items": dates, "latest": dates[-1] if dates else None}


@router.get("/report/daily", summary="销售日报（指标聚合 + 规则预警）")
async def report_daily(
    date: str | None = None,
    service: ReportService = Depends(deps.get_report_service),
) -> dict:
    """指定日期的日报；缺省取数据里最新一天。

    预警由确定性规则引擎产出（库存水位 / 环比骤降 / 差评率 / 广告占比），
    不调用大模型 —— 规则可解释、可单测、零成本。
    """
    return service.build(date).to_dict()


@router.post("/report/daily/sync", summary="日报同步到飞书多维表格")
async def report_daily_sync(
    date: str | None = None,
    service: ReportService = Depends(deps.get_report_service),
) -> dict:
    """把日报写入飞书多维表格。未配置飞书凭证时返回 503 + 补配置指引。"""
    report = service.build(date)
    return service.sync_to_feishu(report)


# ---------------------------------------------------------------------------
# 全链路流水线：拉数 → 日报/预警 →（可选）LLM 解读 → 飞书同步
# ---------------------------------------------------------------------------

@router.post("/pipeline/run", summary="立即执行全链路流水线")
async def pipeline_run(
    force: bool = False,
    service=Depends(deps.get_pipeline_service),
) -> dict:
    """手动触发一次完整链路。

    默认幂等：当日已成功同步则跳过；force=true 强制重跑（会再写一行飞书记录）。
    每一步的结果都在返回值的 steps 里，失败也不抛 500 —— 结构化呈现。
    """
    return await service.run(force=force)


@router.get("/pipeline/status", summary="流水线状态")
async def pipeline_status(
    service=Depends(deps.get_pipeline_service),
) -> dict:
    """调度配置 + 上次运行结果，前端流水线卡片的数据源。"""
    return {
        "enabled": service.settings.pipeline_enabled,
        "schedule": service.settings.pipeline_schedule,
        "refresh_data": service.settings.pipeline_refresh_data,
        "use_llm": service.settings.pipeline_use_llm,
        "last_run": service.last_run,
    }


def _sse(event: dict) -> str:
    """把事件字典序列化为 SSE 帧。"""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
