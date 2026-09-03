"""选品分析 Agent。

职责：基于品类、目标市场、预算给出结构化的选品判断，而不是一句"可以做"。
"""
from __future__ import annotations

from typing import Any

from .base import BaseAgent


class SelectionAgent(BaseAgent):
    domain = "selection"
    name = "selection"
    description = "选品分析：市场机会、竞争强度、风险与进入建议"

    def build_prompt(self, payload: dict[str, Any], knowledge: str) -> tuple[str, str]:
        system = (
            "你是一名资深跨境电商选品分析师，服务过亚马逊、TikTok Shop 等平台卖家。\n"
            "你的判断必须基于给定的选品方法论知识，而不是泛泛而谈。\n"
            "评分要给出理由，不要只给数字。"
        )
        user = (
            f"【检索到的选品方法论】\n{knowledge}\n\n"
            f"【本次选品需求】\n{self.extra_user_context(payload)}\n\n"
            "请给出选品分析，包含：综合评分（0-100）、市场机会、竞争强度、"
            "主要风险、以及明确的进入建议（建议进入 / 谨慎进入 / 不建议进入）。"
        )
        return system, user

    def output_schema(self) -> dict[str, Any]:
        return {
            "score": "number，0-100 综合评分",
            "verdict": "string，只能是：建议进入 / 谨慎进入 / 不建议进入",
            "opportunities": ["string，市场机会点，2-4 条"],
            "competition": {
                "level": "string，只能是：低 / 中 / 高",
                "reason": "string，竞争强度判断依据",
            },
            "risks": ["string，主要风险，2-4 条"],
            "action_items": ["string，下一步行动建议，2-4 条"],
            "summary": "string，100 字以内的结论摘要",
        }

    def _build_query(self, payload: dict[str, Any]) -> str:
        # 选品检索更看重品类和市场，而不是预算数字
        return f"{payload.get('category', '')} {payload.get('market', '')} 选品 市场 竞争"
