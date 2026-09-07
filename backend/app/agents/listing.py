"""Listing 生成 Agent。

职责：把产品卖点转成平台可直接使用的 Listing 文案（标题 / 五点 / 关键词 / A+）。
支持多语言输出 —— 跨境电商的实际刚需，也是这个 Agent 相对"直接问 ChatGPT"的价值点。
"""
from __future__ import annotations

from typing import Any

from .base import BaseAgent

LANG_NAMES = {
    "zh": "中文",
    "en": "英语",
    "de": "德语",
    "fr": "法语",
    "es": "西班牙语",
    "ja": "日语",
}

# 各平台标题字符硬上限（按平台官方规则 + 实测数据）。
# 硬约束写在 Prompt 里比让模型自由发挥可靠 —— 这是把"业务规则"从模型判断里抢回来的工程动作。
PLATFORM_TITLE_LIMITS = {
    "amazon": 200,       # 亚马逊：200 字符，移动端只显示前 60，移动友好关键字必须前置
    "tiktok_shop": 80,   # TikTok Shop：内容电商场景，标题短才清晰
    "shopee": 100,       # Shopee 东南亚：100 字符
    "aliexpress": 50,    # 速卖通：50 字符，极简
    "standalone": 70,    # 独立站 / Shopify：60-70 字符，SEO 友好（meta title 截断线）
}


class ListingAgent(BaseAgent):
    domain = "listing"
    name = "listing"
    description = "Listing 生成：标题、五点描述、搜索关键词、A+ 文案，支持多语言"

    def build_prompt(self, payload: dict[str, Any], knowledge: str) -> tuple[str, str]:
        lang = payload.get("lang", "zh")
        lang_name = LANG_NAMES.get(lang, "中文")
        platform = payload.get("platform", "amazon")
        title_limit = PLATFORM_TITLE_LIMITS.get(platform, 200)
        # 标题字符硬上限写在 system 提示里，让模型在生成时就控制，不要事后让前端再裁剪。
        system = (
            "你是一名跨境电商 Listing 优化专家，熟悉亚马逊、TikTok Shop、Shopee、速卖通、独立站的搜索排名规则。\n"
            f"本次输出语言：{lang_name}。\n"
            f"本次目标平台：{platform}（标题字符上限 {title_limit}）。\n"
            "标题要兼顾关键词覆盖和可读性，五点描述要卖点前置，"
            "关键词要区分核心词和长尾词。"
        )
        user = (
            f"【检索到的 Listing 方法论】\n{knowledge}\n\n"
            f"【产品信息】\n{self.extra_user_context(payload)}\n\n"
            f"请生成 {lang_name} 的 Listing 内容，要求符合目标市场（{platform}）的表达习惯，"
            "不要直译中文。"
            f"标题严格控制在 {title_limit} 字符内。"
        )
        return system, user

    def output_schema(self) -> dict[str, Any]:
        return {
            "title": "string，产品标题，严格遵守目标平台字符上限",
            "bullets": ["string，五点描述，5 条，每条突出一个卖点"],
            "keywords": {
                "core": ["string，核心关键词，3-5 个"],
                "long_tail": ["string，长尾关键词，5-8 个"],
            },
            "a_plus": "string，A+ 页面品牌故事文案，150 字以内",
            "tips": ["string， Listing 优化提示，2-3 条"],
            "platform_compliance": {
                "title_length": "integer，实际生成的标题字符数",
                "title_limit": "integer，目标平台字符上限",
                "pass": "boolean，是否在字符上限内",
            },
        }

    def temperature(self) -> float:
        # 文案生成需要一点创造性
        return 0.6
