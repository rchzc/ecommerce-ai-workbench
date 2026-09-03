"""客服话术 Agent。

职责：把客户的原始问题转成可直接发送的客服回复。
设计要点：客服场景最容易踩的坑是过度承诺（"肯定给您退款"），
所以 Prompt 里明确约束了"不承诺超出政策范围的事"，并让模型输出升级判断。
"""
from __future__ import annotations

from typing import Any

from .base import BaseAgent


class SupportAgent(BaseAgent):
    domain = "support"
    name = "support"
    description = "客服话术：合规回复、情绪安抚、升级判断"

    def build_prompt(self, payload: dict[str, Any], knowledge: str) -> tuple[str, str]:
        lang = payload.get("lang", "zh")
        system = (
            "你是一名跨境电商客服主管，熟悉亚马逊、独立站的售后政策和平台红线。\n"
            "【重要约束】\n"
            "1. 绝不承诺超出平台政策和店铺能力范围的补偿（如无条件全额退款、私下转账）。\n"
            "2. 回复要具体可执行，不要空洞道歉。\n"
            "3. 遇到可能引发差评、纠纷升级、涉及人身安全的情形，必须标记需要人工介入。\n"
            f"回复语言：{'中文' if lang == 'zh' else lang}"
        )
        user = (
            f"【检索到的客服话术规范】\n{knowledge}\n\n"
            f"【客户问题】\n{self.extra_user_context(payload)}\n\n"
            "请给出客服回复话术，包含可直接发送的话术正文、处理要点、以及是否需要人工升级。"
        )
        return system, user

    def output_schema(self) -> dict[str, Any]:
        return {
            "reply": "string，可直接发送给客户的回复话术",
            "tone": "string，语气说明，如：安抚 / 解释 / 致歉 / 拒绝",
            "key_points": ["string，处理要点，2-4 条"],
            "escalate": "boolean，是否需要人工介入",
            "escalate_reason": "string，需要升级的原因；不需要则填空字符串",
            "follow_up": "string，后续跟进建议",
        }

    def temperature(self) -> float:
        return 0.4

    def _build_query(self, payload: dict[str, Any]) -> str:
        return "客服话术 售后 退款 物流延迟 差评处理"
