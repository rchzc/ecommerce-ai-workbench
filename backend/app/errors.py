"""类型化错误体系（本应用对外的统一入口）。

错误契约的**唯一来源是共享包 `ecom_shared.errors`**。原因：数据中台、运行时底座、
本应用层调的是同一个模型网关，"同一个上游故障"必须在哪一层都是同一个异常类、
同一个状态码，前端才能统一处置；否则 A 层报 502、B 层报 500，调用方没法写出
统一的退避/降级策略。

因此本模块**不重新定义任何异常**，只做 re-export。这一点不是洁癖，是一个真踩过的坑：
这里曾经自己定义了一份 `ConfigError`（继承本地 `AppError`），与共享包里的同名类
是两个不同的类对象。结果包内抛出的 `ecom_shared.errors.ConfigError` 在 FastAPI 的
全局处理器里匹配不上 `AppError` 分支 —— 本该返回 503「配置缺失，重试无用」的场景，
实际返回了 500「服务器内部错误」，把"你没填 API Key"这个可自助修复的问题
伪装成了"我们的服务器坏了"。
"""
from __future__ import annotations

# 单一来源：共享包。新增错误类型时改共享包，不要在这里加类。
from ecom_shared.errors import (
    AppError,
    ConfigError,
    ExternalApiError,
    KnowledgeBaseError,
    ModelCallError,
    ModelOutputError,
    NotFoundError,
    RateLimitError,
    ToolError,
    UnauthorizedError,
    ValidationError,
)

__all__ = [
    "AppError",
    "ConfigError",
    "ExternalApiError",
    "KnowledgeBaseError",
    "ModelCallError",
    "ModelOutputError",
    "NotFoundError",
    "RateLimitError",
    "ToolError",
    "UnauthorizedError",
    "ValidationError",
]
