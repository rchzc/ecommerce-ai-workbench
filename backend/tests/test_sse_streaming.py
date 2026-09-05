"""SSE 流式接口的集成测试。

为什么需要这组测试：流式接口是前端打字机效果的唯一数据来源，事件顺序一旦错乱
（例如 knowledge 事件跑到 delta 之后）前端拼接就会错位、丢失知识引用。
这里不依赖真实 LLM，直接通过依赖注入替换 AgentService，断言 SSE 帧的：

  1. 事件顺序严格为 meta → knowledge → delta* → done
  2. delta 帧的文本拼接后等于完整输出（打字机效果的数据正确性）
  3. meta 事件携带模型路由结果（成本看板的数据来源）
  4. 业务异常（AppError）/ 未预期异常都被收敛为 type=error 事件，且流不会中断在半路
  5. /api/ready 探针始终返回 200（Docker HEALTHCHECK 的依赖）
"""
from __future__ import annotations

import json
from contextlib import contextmanager

from fastapi.testclient import TestClient

from app.api import deps
from app.errors import AppError, KnowledgeBaseError
from app.main import app


class _ScriptedService:
    """按预设顺序产出事件，模拟真实 AgentService.stream 的行为。"""

    def __init__(self, events) -> None:
        self._events = events

    async def stream(self, name, payload):
        for event in self._events:
            yield event


class _FailingService:
    """调用 stream 时立即抛出给定异常，用于验证错误收敛逻辑。

    必须是 async generator（含 yield），异常才能在迭代过程中按原类型抛出，
    被路由的 except AppError / except Exception 正确收敛；否则 async for 会把它
    当成"coroutine 不可迭代"的 TypeError，失去原本的错误语义。
    """

    def __init__(self, exc) -> None:
        self._exc = exc

    async def stream(self, name, payload):
        raise self._exc
        yield  # 使该函数成为异步生成器（永不执行到，仅用于语法标记）


def _parse_sse(response) -> list[dict]:
    """把 SSE 响应体解析成事件字典列表（跳过空行与非 data: 行）。"""
    events: list[dict] = []
    for line in response.iter_lines():
        if not line or not line.startswith("data:"):
            continue
        events.append(json.loads(line.removeprefix("data:").strip()))
    return events


@contextmanager
def _with_override(client, fake_service_factory):
    """设置依赖覆盖并在退出时清理，避免污染其他测试。"""
    app.dependency_overrides[deps.get_agent_service] = lambda: fake_service_factory()
    try:
        yield
    finally:
        app.dependency_overrides.clear()


def test_sse_event_ordering_is_meta_knowledge_delta_done():
    events = [
        {
            "type": "meta",
            "model": "qwen-max",
            "tier": "heavy",
            "route_score": 0.81,
            "hits": 3,
            "retrieve_ms": 42.0,
        },
        {
            "type": "knowledge",
            "items": [{"text": "T", "source": "S", "domain": "ads", "score": 0.9}],
        },
        {"type": "delta", "text": "选"},
        {"type": "delta", "text": "品"},
        {"type": "delta", "text": "建议"},
        {"type": "done", "elapsed_ms": 1234.0, "generate_ms": 1100.0},
    ]

    with TestClient(app) as client, _with_override(client, lambda: _ScriptedService(events)):
        with client.stream(
            "POST", "/api/agent/selection/stream", json={"payload": {"niche": "宠物用品"}}
        ) as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            parsed = _parse_sse(resp)

    # 顺序严格为 meta → knowledge → delta* → done
    assert [e["type"] for e in parsed] == [
        "meta",
        "knowledge",
        "delta",
        "delta",
        "delta",
        "done",
    ]


def test_sse_meta_carries_routing_info_and_knowledge_items():
    events = [
        {"type": "meta", "model": "qwen-turbo", "tier": "light", "route_score": 0.2, "hits": 2, "retrieve_ms": 10.0},
        {"type": "knowledge", "items": [{"text": "x", "source": "doc.md", "domain": "ads", "score": 0.88}]},
        {"type": "delta", "text": "ok"},
        {"type": "done", "elapsed_ms": 1.0, "generate_ms": 1.0},
    ]
    with TestClient(app) as client, _with_override(client, lambda: _ScriptedService(events)):
        with client.stream("POST", "/api/agent/ads/stream", json={"payload": {"q": "x"}}) as resp:
            parsed = _parse_sse(resp)

    meta = parsed[0]
    assert meta["model"] == "qwen-turbo"
    assert meta["tier"] == "light"
    assert meta["hits"] == 2
    # 知识引用必须随流下发给前端，否则用户看不到"依据了哪些资料"
    assert parsed[1]["items"][0]["source"] == "doc.md"


def test_sse_delta_fragments_concatenate_to_full_output():
    fragments = ["跨境", "电商", "选品", "三步走"]
    events = [{"type": "meta", "model": "m", "tier": "light", "route_score": 0.1, "hits": 0, "retrieve_ms": 1.0}]
    events += [{"type": "delta", "text": f} for f in fragments]
    events.append({"type": "done", "elapsed_ms": 1.0, "generate_ms": 1.0})

    with TestClient(app) as client, _with_override(client, lambda: _ScriptedService(events)):
        with client.stream("POST", "/api/agent/selection/stream", json={"payload": {"q": "x"}}) as resp:
            parsed = _parse_sse(resp)

    joined = "".join(e["text"] for e in parsed if e["type"] == "delta")
    assert joined == "跨境电商选品三步走"


def test_sse_app_error_is_collapsed_into_error_event_at_end():
    with TestClient(app) as client, _with_override(
        client, lambda: _FailingService(KnowledgeBaseError("知识库暂不可用"))
    ):
        with client.stream("POST", "/api/agent/ads/stream", json={"payload": {"q": "x"}}) as resp:
            parsed = _parse_sse(resp)

    # 业务异常被收敛为 type=error，且是流的最后一帧，不会让流中断在半路
    assert parsed[-1]["type"] == "error"
    assert parsed[-1]["code"] == "knowledge_base_error"
    assert "不可用" in parsed[-1]["message"]


def test_sse_unexpected_exception_is_collapsed_into_internal_error_event():
    with TestClient(app) as client, _with_override(
        client, lambda: _FailingService(RuntimeError("boom"))
    ):
        with client.stream("POST", "/api/agent/ads/stream", json={"payload": {"q": "x"}}) as resp:
            parsed = _parse_sse(resp)

    assert parsed[-1]["type"] == "error"
    assert parsed[-1]["code"] == "internal_error"
    assert "boom" in parsed[-1]["message"]


def test_ready_probe_returns_200_even_when_unconfigured():
    """HEALTHCHECK 依赖 /api/ready 始终 200（配置待补时 ready=false 而非 503）。"""
    with TestClient(app) as client:
        resp = client.get("/api/ready")
        assert resp.status_code == 200
        body = resp.json()
        assert "ready" in body
        assert "configured" in body
