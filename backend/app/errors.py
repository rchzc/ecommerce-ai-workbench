"""类型化错误体系。

设计要点（面试可讲）：
1. 不同错误对应不同 HTTP 状态码：配置缺失 503（服务端没配好，重试无用）、
   模型输出异常 502（上游返回了没法解析的内容）、参数错误 422（客户端的问题）。
2. 错误对外只暴露 code + message，绝不返回堆栈 —— 堆栈会泄露内部路径和依赖版本。
3. 业务代码 raise 类型化错误，全局处理器统一转 HTTP 响应，控制器里不写 try/except。
"""
from __future__ import annotations

from typing import Any


class AppError(Exception):
    """所有业务异常的基类。子类只需覆写 status_code / code。"""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str, detail: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "error": {
                "code": self.code,
                "message": self.message,
            }
        }
        if self.detail is not None:
            payload["error"]["detail"] = self.detail
        return payload


class ConfigError(AppError):
    """配置缺失或不合法 —— 服务端问题，重试无用。"""

    status_code = 503
    code = "config_error"


class ModelOutputError(AppError):
    """模型返回了无法解析的内容 —— 上游异常。"""

    status_code = 502
    code = "model_output_error"


class ModelCallError(AppError):
    """模型调用失败（超时、鉴权失败、限流）。"""

    status_code = 502
    code = "model_call_error"


class KnowledgeBaseError(AppError):
    """知识库不可用（未建库、检索失败、被占用）。"""

    status_code = 503
    code = "knowledge_base_error"


class ValidationError(AppError):
    """输入不合法 —— 客户端问题。"""

    status_code = 422
    code = "validation_error"


class NotFoundError(AppError):
    """资源不存在。"""

    status_code = 404
    code = "not_found"
