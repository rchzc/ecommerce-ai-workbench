"""LLM Gateway：模型接入抽象层。

这是整个项目最核心的一块，四个能力都在这里：

1. 多厂商统一接入：所有 provider 走 OpenAI 兼容协议，业务代码不感知厂商差异。
2. 模型路由：按任务复杂度自动选轻量 / 重量模型，简单任务不占用大模型额度。
3. 三级 JSON 容错解析：模型输出不稳定是常态，必须有兜底，否则前端拿到脏数据会崩。
4. 流式输出：SSE 打字机效果，长任务不用干等。

面试最可能被追问的就是第 2、3 点，代码里都留了对应注释。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from openai import AsyncOpenAI

from ..config import (
    COMPLEX_LENGTH_THRESHOLD,
    HEAVY_HINTS,
    LIGHT_HINTS,
    Settings,
)
from ..errors import ModelCallError, ModelOutputError

logger = logging.getLogger(__name__)

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)

# 厂商不支持 response_format / json_object 时的错误特征
_UNSUPPORTED_MARKERS = (
    "response_format",
    "json_object",
    "json mode",
    "json_mode",
    "unsupported",
    "not supported",
    "unknown parameter",
    "invalid_request",
    "unrecognized request argument",
)


def is_unsupported_json_mode(exc: Exception) -> bool:
    """判断异常是否来自「厂商不支持强制 JSON 模式」这类请求参数问题。

    只有这类错误才值得去掉 response_format 重试一次。

    之前这里捕获所有异常都重试：401 鉴权失败、429 限流、网络超时
    也会被当成"不支持 JSON 模式"，结果是多花一次注定失败的调用，
    还把真正的错误伪装成降级，排查时被误导到完全错误的方向。
    """
    status = getattr(exc, "status_code", None)
    if status is not None:
        try:
            if int(status) not in (400, 404, 415, 422):
                return False
        except (TypeError, ValueError):
            return False
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in text for marker in _UNSUPPORTED_MARKERS)


# ---------------------------------------------------------------------------
# 模型路由
# ---------------------------------------------------------------------------
def classify_complexity(text: str) -> tuple[str, int]:
    """按关键词加权 + 文本长度判定任务复杂度。

    返回 (档位, 得分)。得分 > 0 走重量模型。
    诚实说明：这是规则路由，不是训练出来的分类器，准确率依赖关键词表。
    要更准可以改成先用一个小模型做一次分类，代价是多一次调用。
    """
    score = 0
    for word in HEAVY_HINTS:
        if word in text:
            score += 2
    for word in LIGHT_HINTS:
        if word in text:
            score -= 1
    if len(text) > COMPLEX_LENGTH_THRESHOLD:
        score += 1
    return ("heavy" if score > 0 else "light"), score


# ---------------------------------------------------------------------------
# 三级 JSON 容错解析
# ---------------------------------------------------------------------------
def parse_json_lenient(raw: str) -> dict[str, Any]:
    """三级容错解析模型输出。

    第一级：直接解析。
    第二级：去掉 Markdown 代码块围栏再解析（模型经常包一层 ```json）。
    第三级：截取文本中第一个完整的 {...} 再解析（模型常在 JSON 前后加解释文字）。

    三级都失败就抛 ModelOutputError，由全局处理器转成 502。
    关键原则：宁可报错，也不把脏数据透传给前端。
    """
    text = (raw or "").strip()
    if not text:
        raise ModelOutputError("模型返回内容为空")

    # 第一级
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    # 第二级：去 Markdown 围栏
    fenced = _JSON_FENCE_RE.search(text)
    if fenced:
        try:
            parsed = json.loads(fenced.group(1))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    # 第三级：截取首个完整 JSON 对象（按括号配对扫描，不是简单正则，
    # 因为字符串里也可能出现花括号）
    extracted = _extract_first_object(text)
    if extracted is not None:
        try:
            parsed = json.loads(extracted)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    raise ModelOutputError(
        "模型输出无法解析为 JSON 对象（已尝试三级容错）",
        detail=text[:300],
    )


def _extract_first_object(text: str) -> str | None:
    """按括号配对扫描出第一个完整的 {...}，跳过字符串内的花括号。"""
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for idx in range(start, len(text)):
            ch = text[idx]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : idx + 1]
        start = text.find("{", start + 1)
    return None


# ---------------------------------------------------------------------------
# 用量统计
# ---------------------------------------------------------------------------
@dataclass
class UsageStats:
    """进程内累计的模型用量。用于前端成本看板。"""

    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    by_model: dict[str, int] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def record(self, model: str, prompt: int, completion: int) -> None:
        self.calls += 1
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.by_model[model] = self.by_model.get(model, 0) + prompt + completion

    def snapshot(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "by_model": self.by_model,
        }


# ---------------------------------------------------------------------------
# Gateway
# ---------------------------------------------------------------------------
class LLMGateway:
    """封装模型调用。业务层只跟它打交道，不直接接触 SDK。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.usage = UsageStats()
        self._client = AsyncOpenAI(
            api_key=settings.api_key or "ollama",
            base_url=settings.api_base,
            timeout=settings.request_timeout,
            max_retries=1,
        )

    def resolve_route(
        self, task_text: str, force: str | None = None
    ) -> tuple[str, str, int]:
        """一次算完路由结果，返回 (model, tier, score)。

        流式链路要把 tier/score 下发给前端做成本看板。若先 classify_complexity
        再 resolve_model，同一套关键词扫描会算两遍，且两处逻辑一旦漂移，
        就会出现"看板显示走轻量、实际调用重量"这种自相矛盾的展示。
        """
        if force in ("light", "heavy"):
            model = (
                self.settings.model_light
                if force == "light"
                else self.settings.model_heavy
            )
            return model, force, 0
        tier, score = classify_complexity(task_text)
        model = (
            self.settings.model_light if tier == "light" else self.settings.model_heavy
        )
        return model, tier, score

    def resolve_model(self, task_text: str, force: str | None = None) -> str:
        """选择模型：force 优先，否则按复杂度路由。"""
        return self.resolve_route(task_text, force)[0]

    async def complete(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = True,
        temperature: float = 0.3,
        force_tier: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """非流式调用，返回 (内容, 元信息)。"""
        model = self.resolve_model(f"{system}\n{user}", force_tier)
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "stream": False,
        }
        if json_mode:
            # 百炼 / OpenAI 支持强制 JSON 模式；DeepSeek 不支持，
            # 所以容错解析必须留着，不能依赖这个参数。
            kwargs["response_format"] = {"type": "json_object"}

        try:
            resp = await self._client.chat.completions.create(**kwargs)
        except Exception as exc:  # 统一包装，不让 SDK 异常穿透到控制器
            if not (json_mode and is_unsupported_json_mode(exc)):
                raise ModelCallError(
                    f"模型调用失败（{self.settings.provider_label}）: {exc}"
                ) from exc
            # 厂商不支持强制 JSON 模式：去掉参数重试一次，输出交给三级容错解析兜底
            logger.warning(
                "llm.complete.json_mode_unsupported",
                extra={"model": model, "error": str(exc)[:200]},
            )
            kwargs.pop("response_format")
            try:
                resp = await self._client.chat.completions.create(**kwargs)
            except Exception as exc2:
                raise ModelCallError(
                    f"模型调用失败（{self.settings.provider_label}）: {exc2}"
                ) from exc2

        content = resp.choices[0].message.content or ""
        usage = getattr(resp, "usage", None)
        meta = {
            "model": model,
            "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
        }
        self.usage.record(model, meta["prompt_tokens"], meta["completion_tokens"])
        logger.info("llm.complete", extra={"model": model, **meta})
        return content, meta

    async def stream(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.3,
        force_tier: str | None = None,
    ) -> AsyncIterator[str]:
        """流式调用，逐块产出文本。用于 SSE 打字机效果。

        关键点：必须带上 response_format 强制 JSON 模式。
        否则模型在流式下常常吐出 Python 风格的单引号字典（{'key': 'value'}），
        前端 JSON.parse 会直接失败，只能显示一串原始文本 —— 演示时非常致命。
        """
        model = self.resolve_model(f"{system}\n{user}", force_tier)
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "stream": True,
            "response_format": {"type": "json_object"},
        }
        try:
            stream = await self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            if not is_unsupported_json_mode(exc):
                # 鉴权失败 / 限流 / 超时都不是"不支持 JSON 模式"，重试无意义
                raise ModelCallError(
                    f"模型流式调用失败（{self.settings.provider_label}）: {exc}"
                ) from exc
            # 部分厂商（如 DeepSeek）不支持 response_format，降级为不带该参数重试一次。
            # 此时输出可能是单引号字典，靠三级容错解析兜底。
            logger.warning(
                "llm.stream.json_mode_unsupported",
                extra={"model": model, "error": str(exc)[:200]},
            )
            kwargs.pop("response_format")
            try:
                stream = await self._client.chat.completions.create(**kwargs)
            except Exception as exc2:
                raise ModelCallError(f"模型流式调用失败: {exc2}") from exc2

        collected: list[str] = []
        async for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                collected.append(delta)
                yield delta
        # 流式无法拿到精确 token 数，这里按字符粗估并累计，保证看板有数据
        text = "".join(collected)
        self.usage.record(model, len(system + user) // 2, len(text) // 2)
        logger.info("llm.stream", extra={"model": model, "chars": len(text)})
