"""评论洞察 Agent。

职责：把一堆用户评论提炼成可执行的产品 / 运营改进项。
价值点：人工看 500 条评论要一整天，这里几十秒出结论，且结论是结构化的。
"""
from __future__ import annotations

from typing import Any

from .base import BaseAgent

MAX_REVIEW_CHARS = 4000


class ReviewAgent(BaseAgent):
    domain = "review"
    name = "review"
    description = "评论洞察：情感分布、痛点聚类、产品改进与 Listing 优化建议"

    def build_prompt(self, payload: dict[str, Any], knowledge: str) -> tuple[str, str]:
        system = (
            "你是一名跨境电商用户研究专家，擅长从评论中提炼可执行的改进点。\n"
            "你要区分「产品问题」和「预期管理问题」—— 后者靠改 Listing 描述解决，"
            "前者才需要改产品。这个区分是本 Agent 的核心价值。"
        )
        reviews = str(payload.get("reviews", ""))[:MAX_REVIEW_CHARS]
        user = (
            f"【检索到的评论分析方法论】\n{knowledge}\n\n"
            f"【用户评论原文】\n{reviews}\n\n"
            f"【补充信息】\n{self.extra_user_context({k: v for k, v in payload.items() if k != 'reviews'})}\n\n"
            "请给出评论洞察，包含情感分布、高频痛点（按出现频次排序）、"
            "产品改进建议、Listing 描述优化建议。"
        )
        return system, user

    def output_schema(self) -> dict[str, Any]:
        return {
            "sentiment": {
                "positive": "number，正面占比 0-100",
                "neutral": "number，中性占比 0-100",
                "negative": "number，负面占比 0-100",
            },
            "pain_points": [
                {
                    "topic": "string，痛点主题",
                    "count": "number，出现频次",
                    "type": "string，只能是：产品问题 / 预期管理问题 / 物流问题",
                    "quote": "string，代表性原文摘引",
                }
            ],
            "product_actions": ["string，产品改进建议，2-4 条"],
            "listing_actions": ["string，Listing 描述优化建议，2-4 条"],
            "summary": "string，100 字以内的结论摘要",
        }

    def _build_query(self, payload: dict[str, Any]) -> str:
        # 评论检索应该找"分析方法"，而不是拿评论原文去匹配
        return "评论分析 痛点 归类 差评 改进"
