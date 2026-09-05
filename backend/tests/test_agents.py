"""Agent 层测试：规则预检 + 注册表契约。

注册表契约测试的价值：新增 Agent 时如果漏了 description 或写错 domain，
这里会立刻失败，而不是等到前端渲染出空白面板才被发现。
"""
from __future__ import annotations

from app.agents import AGENT_REGISTRY, create_agent, list_agents
from app.agents.ads import precheck

# data/docs 下的领域目录名
EXPECTED_DOMAINS = {"selection", "listing", "review", "ads", "logistics", "support"}


def test_registry_covers_all_domains():
    assert set(AGENT_REGISTRY) == EXPECTED_DOMAINS


def test_every_agent_declares_domain_name_and_description():
    for name, cls in AGENT_REGISTRY.items():
        assert cls.name == name, f"{cls.__name__}.name 与注册表 key 不一致"
        assert cls.domain in EXPECTED_DOMAINS, f"{name} 的 domain 不在知识库目录里"
        assert cls.description.strip(), f"{name} 缺少 description，前端面板会显示空白"


def test_every_agent_implements_output_schema():
    for name, cls in AGENT_REGISTRY.items():
        schema = cls().output_schema()
        assert isinstance(schema, dict) and schema, f"{name} 的 output_schema 为空"


def test_list_agents_returns_serializable_items():
    items = list_agents()
    assert {i["name"] for i in items} == EXPECTED_DOMAINS
    for item in items:
        assert set(item) == {"name", "domain", "description"}


def test_create_agent_returns_fresh_instance():
    a, b = create_agent("ads"), create_agent("ads")
    assert a is not b
    assert isinstance(a, AGENT_REGISTRY["ads"])


def test_create_unknown_agent_raises_key_error():
    import pytest

    with pytest.raises(KeyError):
        create_agent("nope")


# ------------------------------------------------------------ 规则预检

def test_precheck_flags_high_acos():
    flags = precheck({"acos": 0.42})
    assert any("ACOS" in f for f in flags)


def test_precheck_flags_low_ctr_and_cvr():
    flags = precheck({"ctr": 0.003, "cvr": 0.06})
    assert any("CTR" in f for f in flags)
    assert any("转化率" in f for f in flags)


def test_precheck_healthy_metrics_yield_no_flags():
    assert precheck({"acos": 0.20, "ctr": 0.01, "cvr": 0.12}) == []


def test_precheck_ignores_non_numeric_values():
    """前端 number 输入框可能给出空串或非法值，不能让预检直接抛异常。"""
    assert precheck({"acos": "abc", "ctr": None, "cvr": ""}) == []


def test_precheck_missing_fields_yield_no_flags():
    assert precheck({}) == []


def test_precheck_boolean_is_not_treated_as_number():
    """bool 是 int 的子类，True 会被当成 1.0 触发"ACOS 过高"，属于误判。"""
    flags = precheck({"acos": True})
    assert not any("ACOS" in f for f in flags)
