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
    stream: bool = False

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
