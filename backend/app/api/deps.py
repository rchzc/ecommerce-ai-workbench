"""依赖注入：把应用级单例从 app.state 取出来，交给路由使用。

用依赖注入而不是全局变量的好处：
- 测试时可以轻松替换成 mock 实现
- 组件生命周期由 lifespan 统一管理，不会出现"用到时还没初始化"

这里多做一件事：组件取不到时给出**准确的状态码与原因**。
之前直接 `request.app.state.gateway`，配置校验失败时 lifespan 会提前返回、
state 里根本没有 gateway，于是抛 AttributeError 被兜底成 500「服务内部错误」——
明明是"没配 API Key"这种用户可自行修复的问题，却报成了服务端未知故障，
排查时只能翻日志。现在统一走 _require：配置问题 503 + 明确原因。
"""
from __future__ import annotations

from typing import Any

from fastapi import Request

from ..config import Settings
from ..core.embeddings import Embedder
from ..core.llm import LLMGateway
from ..core.vectorstore import VectorStore
from ..errors import ConfigError
from ..services.agent_service import AgentService, KnowledgeService
from ..services.batch_service import BatchService

_NOT_READY = "服务尚未就绪：组件未完成初始化，请检查启动日志"


def _require(request: Request, name: str) -> Any:
    """按名取组件。缺失时区分「配置错误」与「尚未初始化」，一律 503 + 可读原因。"""
    state = request.app.state
    component = getattr(state, name, None)
    if component is not None:
        return component

    # lifespan 在配置校验失败时会把原因写进 state.config_error，直接透出
    config_error = getattr(state, "config_error", None)
    if config_error:
        raise ConfigError(f"服务未就绪：{config_error}")
    raise ConfigError(_NOT_READY)


def get_gateway(request: Request) -> LLMGateway:
    return _require(request, "gateway")


def get_store(request: Request) -> VectorStore:
    return _require(request, "store")


def get_embedder(request: Request) -> Embedder:
    return _require(request, "embedder")


def get_agent_service(request: Request) -> AgentService:
    return _require(request, "agent_service")


def get_knowledge_service(request: Request) -> KnowledgeService:
    return _require(request, "knowledge_service")


def get_batch_service(request: Request) -> BatchService:
    return _require(request, "batch_service")


def get_settings(request: Request) -> Settings:
    """取运行期配置快照（Webhook 鉴权需要读 WORKFLOW_API_KEY）。"""
    return _require(request, "settings")
