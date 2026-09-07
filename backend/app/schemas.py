"""Pydantic 数据模型：所有输入在边界处校验，不信任客户端数据。"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class AgentRequest(BaseModel):
    """Agent 调用请求。

    payload 是开放字典而非固定字段，因为不同 Agent 的输入字段差异很大；
    具体校验由各 Agent 的 build_prompt 负责（缺字段时会体现在 Prompt 里）。
    但这里仍要做基础防护：非空、字段数上限、单字段长度上限。
    """

    payload: dict[str, Any] = Field(default_factory=dict)

    # 说明：流式走独立的 POST /api/agent/{name}/stream 端点。
    # 之前这里还有个 stream: bool 字段，但没有任何代码读它，
    # 调用方看到会以为传 stream=true 就能切流式，实际仍是阻塞返回 —— 去掉避免误导。

    @field_validator("payload")
    @classmethod
    def _check_payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not value:
            raise ValueError("payload 不能为空")
        if len(value) > 30:
            raise ValueError("payload 字段数过多（上限 30）")
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 64:
                raise ValueError(f"非法字段名: {key!r}")
            if isinstance(item, str) and len(item) > 20000:
                raise ValueError(f"字段 {key} 内容过长（上限 20000 字符）")
        return value


class HealthResponse(BaseModel):
    status: str
    provider: str
    provider_label: str
    model_light: str
    model_heavy: str
    embedding_mode: str
    knowledge_chunks: int
    agents: list[str]


class KbStatsResponse(BaseModel):
    chunks: int
    embedding_mode: str


class RebuildResponse(BaseModel):
    chunks: int
    files: int
    embedding_mode: str
    elapsed_ms: float


# 单单元格最大长度，与 AgentRequest 的字段长度限制保持一致
_BATCH_CELL_MAX = 20000


class BatchRunRequest(BaseModel):
    """批量任务创建请求。

    rows 是"表格的一行对应一个 dict"，字段名就是 CSV 表头。
    上限在 service 层按配置校验（BATCH_MAX_ROWS），这里只拦明显离谱的输入 ——
    边界校验做两道：Pydantic 挡结构与体积，服务层挡业务配额。
    """

    agent: str = Field(min_length=1, max_length=64)
    rows: list[dict[str, Any]] = Field(min_length=1, max_length=1000)
    idempotency_key: str | None = Field(default=None, max_length=128)

    @field_validator("rows")
    @classmethod
    def _check_rows(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for idx, row in enumerate(value):
            if not row:
                raise ValueError(f"第 {idx + 1} 行为空对象")
            if len(row) > 30:
                raise ValueError(f"第 {idx + 1} 行字段过多（上限 30）")
            for key, item in row.items():
                if not isinstance(key, str) or len(key) > 64:
                    raise ValueError(f"第 {idx + 1} 行存在非法字段名: {key!r}")
                if isinstance(item, str) and len(item) > _BATCH_CELL_MAX:
                    raise ValueError(f"第 {idx + 1} 行字段 {key} 内容过长（上限 {_BATCH_CELL_MAX} 字符）")
        return value


class BatchRunResponse(BaseModel):
    job_id: str
    agent: str
    status: str
    total: int


class HookRequest(BaseModel):
    """Webhook 调用请求，结构与单次 Agent 调用一致。"""

    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("payload")
    @classmethod
    def _check_payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not value:
            raise ValueError("payload 不能为空")
        if len(value) > 30:
            raise ValueError("payload 字段数过多（上限 30）")
        return value
