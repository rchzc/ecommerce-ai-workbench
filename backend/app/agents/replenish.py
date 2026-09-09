"""库存补货建议 Agent。

职责：给一条 SKU 的库存快照，输出"要不要补、补多少、为什么"，
并且直接给出可以抄进表格的数字，而不是一段分析文字。

设计上刻意与飞书多维表格的字段对齐（urgency / suggested_qty / summary），
这样外部系统（飞书自动化、n8n）拿到结构化结果后可以直接回写单元格，
不需要再做一次文本解析 —— 这是「AI 能不能嵌进业务流程」的分水岭。

补货量走知识库里的备货公式：
    补货量 = (日均销量 × 补货周期 × 安全系数) - 在库 - 在途
安全系数：常规 1.3 / 旺季 1.6 / 新品 1.2（见 data/docs/logistics/02）
"""
from __future__ import annotations

from typing import Any

from .base import BaseAgent


class ReplenishAgent(BaseAgent):
    domain = "logistics"
    name = "replenish"
    description = "库存补货建议：可用天数判定、建议下单量、补货优先级与断货风险提示"

    def build_prompt(self, payload: dict[str, Any], knowledge: str) -> tuple[str, str]:
        system = (
            "你是一名跨境电商供应链补货规划师，负责给出可执行的补货决策。\n"
            "你必须基于给定的补货量公式和库存快照计算，不要臆造销量或交期。\n"
            "判断优先级：先看可用天数（(在库+在途)/日均销量）会不会断货，"
            "再看补货周期（生产+头程+上架）来不来不及。\n"
            "建议下单量必须是整数；若不需要补货则填 0，不要为了显得专业而硬给数字。"
        )
        user = (
            f"【检索到的备货与物流方法】\n{knowledge}\n\n"
            f"【SKU 库存快照】\n{self.extra_user_context(payload)}\n\n"
            "请给出补货建议，包含：\n"
            "1) urgency：结合可用天数与补货周期判定紧急度\n"
            "2) suggested_qty：按公式算出建议下单量（整数，0 表示暂不补货）\n"
            "3) target_stock_days：本次补货期望覆盖的天数\n"
            "4) reason：写清关键数字（可用天数、补货周期、安全系数），让人能复核\n"
            "5) risks：断货、压货、旺季排仓等风险，2-3 条\n"
            "6) summary：60 字以内结论，可直接贴进表格单元格"
        )
        return system, user

    def output_schema(self) -> dict[str, Any]:
        return {
            "urgency": (
                "string，只能是：紧急补货 / 建议补货 / 继续观察 / 库存充足"
            ),
            "suggested_qty": "number，建议下单量（整数，单位：件；不需要补货填 0）",
            "target_stock_days": "number，本次补货期望覆盖的天数（整数）",
            "reason": "string，判断依据，必须包含可用天数、补货周期、安全系数三个数字",
            "risks": ["string，风险提示，2-3 条"],
            "summary": "string，60 字以内的结论摘要，可直接贴进表格单元格",
        }

    def _build_query(self, payload: dict[str, Any]) -> str:
        # 补货检索要看备货与安全库存，季节性和旺季系数对结果影响很大
        return (
            f"备货 补货 安全库存 断货 {payload.get('sku', '')} "
            f"{payload.get('season', '')} 海外仓 交期"
        )

    def temperature(self) -> float:
        # 补货是算出来的，不是创作出来的：压低温度减少数字漂移
        return 0.2
