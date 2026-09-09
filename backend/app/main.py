"""FastAPI 应用入口。

负责：
1. 应用生命周期（lifespan）：启动时初始化组件并校验配置，关闭时优雅停机
2. 中间件：请求 ID 注入、CORS、安全头
3. 全局异常处理：把类型化错误转成规范 JSON，绝不返回堆栈
4. 静态资源托管：单容器交付时由 FastAPI 统一提供前端产物
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import batch as batch_routes
from .api import routes
from .config import (
    ConfigError,
    load_cors_origins,
    load_log_level,
    load_settings,
    resolve_chroma_dir,
    resolve_static_dir,
)
from .core.embeddings import Embedder
from .core.llm import LLMGateway
from .core.vectorstore import VectorStore
from .errors import AppError
from .logging import Timer, new_request_id, request_id_var, setup_logging
from .services.agent_service import AgentService, KnowledgeService
from .services.batch_service import BatchService
from .services.pipeline_service import PipelineService, scheduler_loop
from .services.report_service import ReportService
from .connectors.feishu_bitable import FeishuBitableConnector

# 路径与开关统一由 config.py 解析（不再在此处散落 os.getenv），
# 保证中间件、lifespan、静态托管读到的都是同一份配置。
PERSIST_DIR = resolve_chroma_dir()
STATIC_DIR = resolve_static_dir()


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """启动初始化 + 关闭清理。"""
    setup_logging(load_log_level())
    logger = logging.getLogger("app.lifespan")

    try:
        settings = load_settings()
    except ConfigError as exc:
        # 配置错误在这里就暴露，而不是等到第一个请求才炸
        logger.error("startup.config_invalid", extra={"error": str(exc)})
        app.state.config_error = str(exc)
        yield
        return

    app.state.config_error = None
    app.state.settings = settings

    store = VectorStore(settings, PERSIST_DIR)
    embedder = Embedder(settings)
    gateway = LLMGateway(settings)

    app.state.store = store
    app.state.embedder = embedder
    app.state.gateway = gateway
    app.state.agent_service = AgentService(
        gateway=gateway, store=store, embedder=embedder, top_k=settings.top_k
    )
    app.state.knowledge_service = KnowledgeService(
        store=store, embedder=embedder, settings=settings
    )
    # 批量任务服务：持有后台任务，跨越请求生命周期，因此挂在 app.state 上而不是每次新建
    app.state.batch_service = BatchService(
        agent_service=app.state.agent_service,
        max_rows=settings.batch_max_rows,
        concurrency=settings.batch_concurrency,
    )
    # 日报服务：飞书凭证齐备时挂真实连接器，否则不挂（同步端点会 503 给出补配置指引）。
    # 日报生成与看板不依赖飞书，属于可选出口 —— 不能让"没接飞书"阻断核心链路。
    app.state.report_service = ReportService(
        settings=settings,
        feishu=(
            FeishuBitableConnector(
                app_id=settings.feishu_app_id,
                app_secret=settings.feishu_app_secret,
                bitable_app_token=settings.feishu_bitable_token,
                table_id=settings.feishu_table_id,
            )
            if settings.feishu_configured
            else None
        ),
    )
    # 全链路流水线：编排「拉数 → 日报/预警 → 可选 LLM 解读 → 飞书同步」
    app.state.pipeline_service = PipelineService(
        settings=settings,
        report_service=app.state.report_service,
        gateway=gateway,
    )
    # 定时调度：PIPELINE_ENABLED=true 时每天 PIPELINE_SCHEDULE 时刻自动执行
    pipeline_task = None
    if settings.pipeline_enabled:
        pipeline_task = asyncio.create_task(
            scheduler_loop(app.state.pipeline_service, settings.pipeline_schedule)
        )
        app.state.pipeline_task = pipeline_task

    logger.info(
        "startup.ready",
        extra={
            "provider": settings.provider,
            "model_light": settings.model_light,
            "model_heavy": settings.model_heavy,
            "embedding": embedder.mode,
            "chunks": store.count(),
        },
    )

    yield

    # 优雅停机：先停定时调度循环，再走组件清理
    pipeline_task = getattr(app.state, "pipeline_task", None)
    if pipeline_task is not None:
        pipeline_task.cancel()
        try:
            await pipeline_task
        except asyncio.CancelledError:
            pass

    logger.info("shutdown.complete", extra={"calls": gateway.usage.calls})


def create_app() -> FastAPI:
    app = FastAPI(
        title="跨境电商 AI 运营工作台",
        description="RAG + 多 Agent 的跨境电商运营决策系统",
        version="2.0.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    # --- 中间件 -------------------------------------------------------
    cors_origins = load_cors_origins()
    if "*" in cors_origins:
        # 不阻断启动（本地开发依赖通配），但留痕提醒：生产应显式列出来源
        logging.getLogger("app.startup").warning(
            "cors.wildcard_enabled",
            extra={"hint": "生产环境请在 CORS_ORIGINS 中指定显式来源，不要用 *"},
        )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        """注入请求 ID、记录耗时、添加安全头。"""
        rid = new_request_id()
        token = request_id_var.set(rid)
        with Timer() as timer:
            try:
                response = await call_next(request)
            except Exception:
                logging.getLogger("app.request").exception("request.crashed")
                response = JSONResponse(
                    status_code=500,
                    content={
                        "error": {
                            "code": "internal_error",
                            "message": "服务内部错误",
                            "request_id": rid,
                        }
                    },
                )
        request_id_var.reset(token)
        response.headers["X-Request-ID"] = rid
        response.headers["X-Response-Time"] = f"{timer.elapsed_ms}ms"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        logging.getLogger("app.request").info(
            "request.done",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "elapsed_ms": timer.elapsed_ms,
            },
        )
        return response

    # --- 全局异常处理 ---------------------------------------------------
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError):
        """类型化错误 → 规范 JSON。只暴露 code/message，不返回堆栈。"""
        payload = exc.to_dict()
        payload["error"]["request_id"] = request_id_var.get()
        logging.getLogger("app.error").warning(
            "app_error",
            extra={"code": exc.code, "status": exc.status_code, "path": request.url.path},
        )
        return JSONResponse(status_code=exc.status_code, content=payload)

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception):
        """兜底处理：未预期异常也返回规范结构，绝不泄露堆栈。"""
        logging.getLogger("app.error").exception("unexpected_error")
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "服务内部错误",
                    "request_id": request_id_var.get(),
                }
            },
        )

    # --- 路由 -----------------------------------------------------------
    app.include_router(routes.router, prefix="/api")
    app.include_router(batch_routes.router, prefix="/api")

    @app.get("/api/config/error")
    async def config_error():
        """配置错误时前端可拉取具体原因，避免只看到 503 一脸茫然。"""
        message = getattr(app.state, "config_error", None)
        return {"configured": message is None, "message": message}

    @app.get("/api/ready", summary="就绪探针")
    async def ready():
        """容器就绪探针：不触碰任何业务组件，配置有问题时也返回 200 + ready=false。

        与 /api/health 的区别：health 会真实访问模型配置和向量库（依赖已就绪），
        适合人工排查；ready 只判断"能不能开始接客"，适合编排系统轮询。
        """
        message = getattr(app.state, "config_error", None)
        components = (
            "gateway",
            "store",
            "embedder",
            "agent_service",
            "knowledge_service",
            "report_service",
            "pipeline_service",
        )
        components_ready = all(
            getattr(app.state, name, None) is not None for name in components
        )
        return {
            "ready": message is None and components_ready,
            "configured": message is None,
            "message": message,
        }

    # --- 静态资源（单容器交付）--------------------------------------------
    if os.path.isdir(STATIC_DIR):
        app.mount("/assets", StaticFiles(directory=os.path.join(STATIC_DIR, "assets")), name="assets")

        @app.get("/")
        async def index():
            return FileResponse(os.path.join(STATIC_DIR, "index.html"))

        @app.get("/{full_path:path}")
        async def spa_fallback(full_path: str):
            """前端路由兜底：非 API 路径一律返回 index.html。"""
            target = os.path.join(STATIC_DIR, full_path)
            if os.path.isfile(target):
                return FileResponse(target)
            return FileResponse(os.path.join(STATIC_DIR, "index.html"))
    else:
        @app.get("/")
        async def dev_index():
            return {
                "message": "后端运行中（未检测到前端构建产物）",
                "docs": "/api/docs",
                "health": "/api/health",
            }

    return app


app = create_app()
