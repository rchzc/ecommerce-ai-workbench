"""重排与模型路由的测试。

这两块都是"规则驱动"的逻辑，改一个常数就可能悄悄改变线上行为，
所以把判定边界钉在测试里。
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.config import COMPLEX_LENGTH_THRESHOLD, Settings
from app.core.llm import classify_complexity
from app.core.rerank import lexical_score, rerank


@dataclass
class _C:
    text: str
    score: float = 0.5
    source: str = "t.md"


# ------------------------------------------------------------------ rerank

def test_lexical_score_full_and_zero_hit():
    assert lexical_score("退款", "退款流程说明") == 1.0
    assert lexical_score("退款", "物流时效说明") == 0.0


def test_lexical_score_case_insensitive_for_ascii():
    assert lexical_score("ACOS", "acos 过高") == 1.0


def test_lexical_score_ignores_repeated_query_tokens():
    """query 里重复出现的词不应抬高分数（用集合去重）。"""
    assert lexical_score("退款 退款 退款", "退款") == 1.0


def test_lexical_score_empty_query_returns_zero():
    assert lexical_score("", "任意内容") == 0.0


def test_rerank_promotes_lexical_hit_over_slightly_higher_semantic():
    """语义分略高但无关键词的片段，应被字面命中的片段反超。"""
    cands = [
        _C("物流时效和海运清关的关系", score=0.66),
        _C("客户要求退款时的合规话术", score=0.60),
    ]
    out = rerank("退款话术怎么写", cands, top_k=2)
    assert "退款" in out[0].text


def test_rerank_respects_top_k():
    cands = [_C(f"内容{i}", score=0.1 * i) for i in range(5)]
    assert len(rerank("内容", cands, top_k=2)) == 2


def test_rerank_top_k_is_clamped_to_at_least_one():
    cands = [_C("a", score=0.1), _C("b", score=0.2)]
    assert len(rerank("a", cands, top_k=0)) == 1


def test_rerank_empty_candidates():
    assert rerank("任意", [], top_k=3) == []


def test_rerank_rewrites_score_to_fused_value():
    c = _C("退款话术", score=0.60)
    (out,) = rerank("退款话术", [c], top_k=1)
    # alpha=0.7 语义 + 0.3 词面，词面满命中 → 0.7*0.6 + 0.3*1.0 = 0.72
    assert out.score == pytest.approx(0.72, abs=1e-4)


def test_rerank_alpha_zero_purely_lexical():
    cands = [_C("退款话术", score=0.10), _C("无关内容", score=0.99)]
    out = rerank("退款话术", cands, top_k=1, alpha=0.0)
    assert "退款" in out[0].text


# --------------------------------------------------------------- 模型路由

def test_heavy_keyword_selects_heavy_tier():
    tier, score = classify_complexity("请分析这个品类的竞争格局")
    assert tier == "heavy"
    assert score > 0


def test_light_keyword_selects_light_tier():
    tier, score = classify_complexity("判断是否属于违禁品")
    assert tier == "light"
    assert score <= 0


def test_long_text_is_treated_as_complex_even_without_keywords():
    text = "x" * (COMPLEX_LENGTH_THRESHOLD + 1)
    tier, _ = classify_complexity(text)
    assert tier == "heavy"


def test_empty_text_falls_back_to_light():
    tier, score = classify_complexity("")
    assert (tier, score) == ("light", 0)


def _settings() -> Settings:
    return Settings(
        provider="dashscope",
        api_key="test-key",
        api_base="http://example.invalid/v1",
        model_light="light-model",
        model_heavy="heavy-model",
        model_embedding="emb-model",
        supports_embedding=True,
        top_k=4,
        chunk_size=600,
        chunk_overlap=50,
        request_timeout=30,
        cors_origins=["*"],
        log_level="INFO",
        chroma_dir="/tmp/chroma",
        static_dir="/tmp/static",
        workflow_api_key="",
        batch_max_rows=200,
        batch_concurrency=3,
    )


def test_resolve_route_returns_consistent_model_and_tier():
    """看板展示的 tier 与实际调用的 model 必须来自同一次决策。"""
    from app.core.llm import LLMGateway

    gw = LLMGateway(_settings())
    model, tier, score = gw.resolve_route("请诊断广告投放问题")
    assert tier == "heavy"
    assert model == "heavy-model"

    model2, tier2, _ = gw.resolve_route("判断是否违规")
    assert tier2 == "light"
    assert model2 == "light-model"


def test_resolve_route_force_tier_overrides_scoring():
    from app.core.llm import LLMGateway

    gw = LLMGateway(_settings())
    assert gw.resolve_route("请深度分析", force="light") == ("light-model", "light", 0)
    assert gw.resolve_route("判断一下", force="heavy") == ("heavy-model", "heavy", 0)


def test_resolve_model_matches_resolve_route():
    from app.core.llm import LLMGateway

    gw = LLMGateway(_settings())
    text = "请给出优化方案"
    assert gw.resolve_model(text) == gw.resolve_route(text)[0]
