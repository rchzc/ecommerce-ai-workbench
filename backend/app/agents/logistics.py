"""物流方案 Agent。

职责：按目的地、重量、时效要求给出物流方案对比与选择建议。
跨境电商物流是真实痛点：海运便宜但慢、空运快但贵、海外仓有备货压力。
"""
from __future__ import annotations

from typing import Any

from .base import BaseAgent


class LogisticsAgent(BaseAgent):
    domain = "logistics"
    name = "logistics"
    description = "物流方案：渠道对比、成本与时效估算、备货与风险提示"

    def build_prompt(self, payload: dict[str, Any], knowledge: str) -> tuple[str, str]:
        system = (
            "你是一名跨境电商物流规划师，熟悉海运整柜/拼箱、空运、国际快递、"
            "海外仓一件代发等渠道的成本结构和适用场景。\n"
            "你必须给出可对比的方案（至少 2 个），并明确推荐其中一个及理由。"
            "所有方案的数字必须基于知识库方法论，不臆造。"
            "为每个渠道给出 days / cost_per_kg / cutoff_days 三个可比较字段，"
            "方便运营抄进 Excel。"
        )
        user = (
            f"【检索到的物流方法论】\n{knowledge}\n\n"
            f"【发货需求】\n{self.extra_user_context(payload)}\n\n"
            "请给出物流方案，包含：\n"
            "1) 至少 2 个可选渠道的对比\n"
            "2) 每个渠道填充 matrix 字段：days（X-X 天）、cost_per_kg（¥/$/kg）、cutoff_days（距离黑五等的可准备窗口）\n"
            "3) 推荐其中一个 + 理由\n"
            "4) 风险提示（旺季排仓 / 清关 / 关税 / 单押一渠道的高风险）"
        )
        return system, user

    def output_schema(self) -> dict[str, Any]:
        return {
            "options": [
                {
                    "channel": "string，渠道名称，如：海运拼箱 / 空运 / 国际快递 / 海外仓",
                    "cost_estimate": "string，成本估算",
                    "eta": "string，时效估算",
                    "pros": ["string，优势"],
                    "cons": ["string，劣势"],
                }
            ],
            # 时效-成本矩阵：把"X 天到达 / Y $/kg"量化到一张表，运营可直接对照预算做选择
            "matrix": [
                {
                    "channel": "string，渠道名",
                    "days": "string，到达时间范围，如 '25-35'",
                    "cost_per_kg": "string，单价成本区间，如 '¥15-25/kg'",
                    "cutoff_days": "integer，相对出运的最迟截止天数（用于倒推备货节点）",
                }
            ],
            "recommended": "string，推荐渠道名称（必须是 options 中的一个）",
            "reason": "string，推荐理由",
            "risks": ["string，风险提示，2-3 条"],
            "summary": "string，100 字以内的结论摘要",
        }

    def _build_query(self, payload: dict[str, Any]) -> str:
        return f"物流 {payload.get('destination', '')} 头程 成本 时效 清关"
