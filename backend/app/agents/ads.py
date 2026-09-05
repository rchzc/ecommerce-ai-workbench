"""广告诊断 Agent。

职责：根据广告投放指标定位问题并给出优化动作。
设计要点：这里做的是「规则打底 + 模型解读」—— 指标异常先由规则定位到具体环节，
再让模型结合方法论给出动作，避免模型凭感觉输出"优化一下关键词"这种废话。
"""
from __future__ import annotations

import math
from typing import Any

from .base import BaseAgent

# 行业经验阈值，用于规则预检（会作为上下文喂给模型）
THRESHOLDS = {
    "acos_high": 0.35,
    "ctr_low": 0.004,
    "cvr_low": 0.08,
}


def _as_number(value: Any) -> float | None:
    """把输入转成数值，非数值返回 None。

    必须显式排除 bool：它是 int 的子类，`isinstance(True, int)` 为真，
    直接取值会把 True 当成 1.0，进而误判成"ACOS 100%，严重亏损"。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    # NaN 参与比较永远为 False，会让异常指标静默通过，这里直接当缺失处理
    return None if math.isnan(result) else result


def precheck(metrics: dict[str, Any]) -> list[str]:
    """规则预检：先把明显的指标异常挑出来。"""
    flags: list[str] = []
    acos = _as_number(metrics.get("acos"))
    ctr = _as_number(metrics.get("ctr"))
    cvr = _as_number(metrics.get("cvr"))

    if acos is not None and acos > THRESHOLDS["acos_high"]:
        flags.append(f"ACOS {acos:.1%} 高于健康阈值 {THRESHOLDS['acos_high']:.0%}，投放亏损风险高")
    if ctr is not None and ctr < THRESHOLDS["ctr_low"]:
        flags.append(f"CTR {ctr:.2%} 低于 {THRESHOLDS['ctr_low']:.2%}，主图或标题吸引力不足")
    if cvr is not None and cvr < THRESHOLDS["cvr_low"]:
        flags.append(f"转化率 {cvr:.1%} 低于 {THRESHOLDS['cvr_low']:.0%}，落地页或价格存在问题")
    return flags


class AdsAgent(BaseAgent):
    domain = "ads"
    name = "ads"
    description = "广告诊断：指标异常定位、问题归因、分优先级优化动作"

    def build_prompt(self, payload: dict[str, Any], knowledge: str) -> tuple[str, str]:
        flags = precheck(payload)
        flags_text = "\n".join(f"- {f}" for f in flags) or "- 未发现明显指标异常"
        system = (
            "你是一名跨境电商广告投放优化师，熟悉亚马逊 SP/SB/SD 广告和 TikTok 投放。\n"
            "你的诊断必须落到具体动作上（调整哪个投放、改什么、预期多久见效），"
            "不要给「加强运营」这类无法执行的建议。"
        )
        user = (
            f"【检索到的广告优化方法论】\n{knowledge}\n\n"
            f"【规则预检结果】\n{flags_text}\n\n"
            f"【广告投放数据】\n{self.extra_user_context(payload)}\n\n"
            "请给出广告诊断，包含核心问题定位、归因分析、以及按优先级排序的优化动作。"
        )
        return system, user

    def output_schema(self) -> dict[str, Any]:
        return {
            "verdict": "string，只能是：健康 / 需优化 / 严重亏损",
            "issues": [
                {
                    "area": "string，问题环节，如：关键词 / 主图 / 出价 / 落地页",
                    "severity": "string，只能是：高 / 中 / 低",
                    "description": "string，问题描述",
                }
            ],
            "actions": [
                {
                    "action": "string，具体动作",
                    "priority": "string，只能是：P0 / P1 / P2",
                    "expected_effect": "string，预期效果",
                    "eta": "string，见效周期，如 3-7 天",
                }
            ],
            "budget_advice": "string，预算调整建议",
            "summary": "string，100 字以内的结论摘要",
        }

    def _build_query(self, payload: dict[str, Any]) -> str:
        issues = precheck(payload)
        base = "广告优化 ACOS 转化率 投放策略"
        if issues:
            return base + " " + " ".join(issues)
        return base
