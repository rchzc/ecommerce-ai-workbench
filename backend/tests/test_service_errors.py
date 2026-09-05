"""错误类型化与服务层的测试。

这里覆盖的是"报错是否报得对"：同样一个故障，报 500「服务内部错误」
和报 503「未找到任何 .md 文档」，对排查者是完全不同的成本。
"""
from __future__ import annotations

import pytest

from app.config import Settings, load_settings
from app.errors import ConfigError, KnowledgeBaseError, NotFoundError
from app.services.agent_service import AgentService, KnowledgeService


class _FakeStore:
    def __init__(self) -> None:
        self.added = 0
        self.resetted = False

    def count(self) -> int:
        return self.added

    def reset(self) -> None:
        self.resetted = True
        self.added = 0

    def add(self, **kwargs) -> None:
        self.added += len(kwargs["ids"])


class _FakeEmbedder:
    mode = "fake:test"

    async def embed(self, texts):
        return [[0.1, 0.2, 0.3] for _ in texts]


def _settings(**overrides) -> Settings:
    base = dict(
        provider="dashscope",
        api_key="test-key",
        api_base="http://example.invalid/v1",
        model_light="light",
        model_heavy="heavy",
        model_embedding="emb",
        supports_embedding=True,
        top_k=4,
        chunk_size=600,
        chunk_overlap=50,
        request_timeout=30,
        cors_origins=["*"],
        log_level="INFO",
        chroma_dir="/tmp/chroma",
        static_dir="/tmp/static",
    )
    base.update(overrides)
    return Settings(**base)


# ---------------------------------------------------------- 配置校验

def test_missing_api_key_raises_config_error(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "dashscope")
    for key in ("LLM_API_KEY", "API_KEY", "DASHSCOPE_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ConfigError):
        load_settings()


def test_ollama_does_not_require_api_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    settings = load_settings()
    assert settings.provider == "ollama"


def test_unknown_provider_raises(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "not-a-provider")
    monkeypatch.setenv("LLM_API_KEY", "k")
    with pytest.raises(ConfigError):
        load_settings()


def test_invalid_numeric_config_raises(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "dashscope")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("TOP_K", "not-a-number")
    with pytest.raises(ConfigError):
        load_settings()


def test_overlap_must_be_smaller_than_chunk_size(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "dashscope")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("CHUNK_SIZE", "100")
    monkeypatch.setenv("CHUNK_OVERLAP", "100")
    with pytest.raises(ConfigError):
        load_settings()


def test_top_k_must_be_positive(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "dashscope")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("TOP_K", "0")
    with pytest.raises(ConfigError):
        load_settings()


def test_cors_origins_are_split_and_trimmed(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "dashscope")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("CORS_ORIGINS", "http://a.com, http://b.com ,")
    settings = load_settings()
    assert settings.cors_origins == ["http://a.com", "http://b.com"]


# ---------------------------------------------------------- Agent 服务

def test_unknown_agent_raises_not_found():
    service = AgentService(
        gateway=None, store=None, embedder=None, top_k=4  # type: ignore[arg-type]
    )
    with pytest.raises(NotFoundError) as exc:
        service._get_agent("does-not-exist")
    assert "does-not-exist" in str(exc.value)


def test_known_agent_is_resolved():
    service = AgentService(
        gateway=None, store=None, embedder=None, top_k=4  # type: ignore[arg-type]
    )
    agent = service._get_agent("ads")
    assert agent.name == "ads"
    assert agent.domain == "ads"


def test_available_agents_lists_all_registered():
    service = AgentService(
        gateway=None, store=None, embedder=None, top_k=4  # type: ignore[arg-type]
    )
    names = {a["name"] for a in service.available_agents()}
    assert {"selection", "listing", "review", "ads", "logistics", "support"} <= names


# ---------------------------------------------------------- 知识库服务

@pytest.mark.asyncio
async def test_rebuild_without_settings_raises_config_error():
    service = KnowledgeService(store=_FakeStore(), embedder=_FakeEmbedder())
    with pytest.raises(ConfigError):
        await service.rebuild()


@pytest.mark.asyncio
async def test_rebuild_without_documents_raises_knowledge_base_error(tmp_path):
    service = KnowledgeService(
        store=_FakeStore(),
        embedder=_FakeEmbedder(),
        settings=_settings(),
        docs_dir=str(tmp_path / "empty"),
    )
    with pytest.raises(KnowledgeBaseError):
        await service.rebuild()


@pytest.mark.asyncio
async def test_rebuild_writes_chunks_and_reports_stats(tmp_path):
    docs = tmp_path / "ads"
    docs.mkdir(parents=True)
    (docs / "a.md").write_text("广告方法论第一段。\n\n广告方法论第二段。", encoding="utf-8")

    store = _FakeStore()
    service = KnowledgeService(
        store=store,
        embedder=_FakeEmbedder(),
        settings=_settings(),
        docs_dir=str(tmp_path),
    )
    result = await service.rebuild()

    assert result["files"] == 1
    assert result["chunks"] >= 2
    assert store.added == result["chunks"]
    assert store.resetted is True
    assert result["embedding_mode"] == "fake:test"
    assert result["elapsed_ms"] >= 0
