"""Agent 注册表。

新增一个 Agent 的完整步骤：
    1. 在 agents/ 下新建一个文件，继承 BaseAgent，实现 build_prompt + output_schema
    2. 在下面的 AGENT_REGISTRY 里加一行

不需要改任何路由、服务或前端代码 —— 前端的面板列表也是从 /agents 接口动态拉的。
这就是把公共链路收敛到基类带来的实际收益。
"""
from __future__ import annotations

from .ads import AdsAgent
from .base import BaseAgent
from .listing import ListingAgent
from .logistics import LogisticsAgent
from .review import ReviewAgent
from .selection import SelectionAgent
from .support import SupportAgent

AGENT_REGISTRY: dict[str, type[BaseAgent]] = {
    "selection": SelectionAgent,
    "listing": ListingAgent,
    "review": ReviewAgent,
    "ads": AdsAgent,
    "logistics": LogisticsAgent,
    "support": SupportAgent,
}


def create_agent(name: str) -> BaseAgent:
    """按名称创建 Agent 实例。未知名称抛 KeyError，由路由层转 404。"""
    if name not in AGENT_REGISTRY:
        raise KeyError(name)
    return AGENT_REGISTRY[name]()


def list_agents() -> list[dict[str, str]]:
    """列出所有可用 Agent，供前端动态渲染面板。"""
    return [
        {
            "name": name,
            "domain": cls.domain,
            "description": cls.description,
        }
        for name, cls in AGENT_REGISTRY.items()
    ]


__all__ = [
    "AGENT_REGISTRY",
    "BaseAgent",
    "create_agent",
    "list_agents",
]
