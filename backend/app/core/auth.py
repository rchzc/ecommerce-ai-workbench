"""对外接口的 API Key 鉴权。

为什么单独做鉴权：
    批量和 Agent 接口是给内部前端用的（同源、不鉴权，与既有 /api/agent 一致），
    但 Webhook 端点要暴露给 n8n / Dify / Coze 这类外部编排工具调用 ——
    一旦对外开放，就必须有凭据，否则任何人拿到地址都能刷你的模型额度。

安全要点：
    1. 用 secrets.compare_digest 做常量时间比较，避免通过响应耗时逐字符爆破 Key。
    2. Key 不配置时直接报错（503），**不做静默放行** ——
       "没配 Key 就当没鉴权"是很多内部系统被刷的起点。
    3. Key 只从环境变量读，不进代码库，也不写进日志。
"""
from __future__ import annotations

import secrets

from ..errors import ConfigError, UnauthorizedError


def verify_api_key(provided: str | None, expected: str) -> None:
    """校验请求携带的 API Key。

    Args:
        provided: 请求头里的 Key（X-API-Key）
        expected: 服务端配置的 Key（settings.workflow_api_key）

    Raises:
        ConfigError: 服务端未配置 Key（503，属于服务端未就绪）
        UnauthorizedError: Key 缺失或不匹配（401）
    """
    if not expected:
        raise ConfigError(
            "未配置 WORKFLOW_API_KEY，Webhook 端点不可用。"
            "请在 backend/.env 中设置 WORKFLOW_API_KEY 后重启服务。"
        )
    if not provided:
        raise UnauthorizedError("缺少 X-API-Key 请求头")
    if not secrets.compare_digest(provided, expected):
        raise UnauthorizedError("API Key 无效")
