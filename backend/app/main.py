"""FastAPI 应用入口。

负责：
1. 应用生命周期（lifespan）：启动时初始化组件并校验配置，关闭时优雅停机
2. 中间件：请求 ID 注入、CORS、安全头
3. 全局异常处理：把类型化错误转成规范 JSON，绝不返回堆栈
4. 静态资源托管：单容器交付时由 FastAPI 统一提供前端产物
"""
from __future__ import annotations

import contextlib
import logging
import os
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import routes
from .config import ConfigError, load_settings
from .core.embeddings import Embedder
from .core.llm import LLMGateway
from .core.vectorstore import VectorStore
from .errors import AppError
from .logging import Timer, new_request_id, request_id_var, setup_logging
from .services.agent_service import AgentService, KnowledgeService

PERSIST_DIR = os.getenv("CHROMA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "chroma_db"
)
STATIC_DIR = os.getenv("STATIC_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "frontend",
    "dist",
)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """启动初始化 + 关闭清理。"""
    setup_logging(os.getenv("LOG_LEVEL", "INFO"))
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
    app.state.knowledge_service = KnowledgeService(store=store, embedder=embedder)

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
    app.add_middleware(
        CORSMiddleware,
        allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
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

    @app.get("/api/config/error")
    async def config_error():
        """配置错误时前端可拉取具体原因，避免只看到 503 一脸茫然。"""
        message = getattr(app.state, "config_error", None)
        return {"configured": message is None, "message": message}

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
