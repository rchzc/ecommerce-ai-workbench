"""依赖注入：把应用级单例从 app.state 取出来，交给路由使用。

用依赖注入而不是全局变量的好处：
- 测试时可以轻松替换成 mock 实现
- 组件生命周期由 lifespan 统一管理，不会出现"用到时还没初始化"
"""
from __future__ import annotations

from fastapi import Request

from ..core.embeddings import Embedder
from ..core.llm import LLMGateway
from ..core.vectorstore import VectorStore
from ..services.agent_service import AgentService, KnowledgeService


def get_gateway(request: Request) -> LLMGateway:
    return request.app.state.gateway


def get_store(request: Request) -> VectorStore:
    return request.app.state.store


def get_embedder(request: Request) -> Embedder:
    return request.app.state.embedder


def get_agent_service(request: Request) -> AgentService:
    return request.app.state.agent_service


def get_knowledge_service(request: Request) -> KnowledgeService:
    return request.app.state.knowledge_service
