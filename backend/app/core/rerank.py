"""检索重排：向量召回 + 词面重排的轻量融合。

背景（面试可讲）：纯向量召回按语义相似度排序，但中文短 query 常有
"同义不同词"或"字面命中却被排到后面"的问题。重排在召回的候选里用
"词面重叠"做二次打分，把真正含有关键词的片段提上来，缓解噪声。

设计原则：
- 纯本地、无外部依赖、不联网、确定可复现 —— 评测脚本可在沙箱直接跑。
- 融合分 = α·语义分 + (1-α)·词面分。语义分来自向量库相似度（已归一化到 0~1），
  词面分来自 query 与 chunk 的字符/词重叠率，对中文友好。
- 不 import vectorstore：本模块只依赖传入对象的 .text / .score 字段（鸭子类型），
  这样离线评测脚本不需要加载 chromadb 也能复用同一套打分逻辑。
"""
from __future__ import annotations

import re
from typing import Any, Sequence

# 语义分权重。0.7 让向量召回主导方向，0.3 的词面分用于"字面命中却被排后"的纠偏。
DEFAULT_ALPHA = 0.7

# 英文/数字词保留原词，中文按单字切（中文无空格，字符级重叠更稳）。
_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+|[一-鿿]")


def _tokenize(text: str) -> list[str]:
    """把文本切成可比较的 token：ASCII 词 + 汉字单字，统一小写。"""
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def lexical_score(query: str, chunk_text: str) -> float:
    """query 与 chunk 的词面重叠率（命中 token 种类数 / query token 种类数）。

    用集合去重，避免 query 里重复出现的词抬高分数；chunk 里命中即算。
    返回 0~1。query 无有效 token 时返回 0（不强行打分）。
    """
    q_tokens = set(_tokenize(query))
    if not q_tokens:
        return 0.0
    text = chunk_text.lower()
    hit = sum(1 for t in q_tokens if t in text)
    return hit / len(q_tokens)


def rerank(
    query: str,
    candidates: Sequence[Any],
    *,
    top_k: int,
    alpha: float = DEFAULT_ALPHA,
) -> list[Any]:
    """在召回候选里做轻量重排，返回前 top_k。

    candidates 需具备 .text（原文）与 .score（语义相似度，0~1）两个字段；
    返回的是同一批对象（按融合分排序），并把 .score 原地改写为融合分，
    使前端展示的"相关度"反映重排后的结果。

    注意：只重排序、不跨域。调用方应在传入前就按 domain 过滤好候选。
    """
    if not candidates:
        return []
    if top_k < 1:
        top_k = 1

    scored: list[tuple[float, Any]] = []
    for c in candidates:
        lex = lexical_score(query, c.text)
        fused = alpha * float(getattr(c, "score", 0.0)) + (1 - alpha) * lex
        # 原地改写 score，让下游透明拿到重排后的相关度
        try:
            c.score = round(fused, 4)
        except (AttributeError, TypeError):
            pass
        scored.append((fused, c))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored[:top_k]]


# ---------------------------------------------------------------------------
# 自测：不依赖 chromadb / 网络，直接跑即可验证重排逻辑把"字面命中"片段提上来。
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from dataclasses import dataclass

    @dataclass
    class _C:
        text: str
        score: float = 0.5
        source: str = "self-test"

    # 场景：query 含"退款/话术"，一个语义分略高(0.66)但无关键词的干扰片段，
    # 和一个语义分接近(0.60)、但字面命中"退款/话术"的片段。
    # rerank 作为"纠偏器"，应把字面命中的片段提上来（而非无脑跟语义分）。
    cand = [
        _C("客户要求退款时的合规话术与升级判断流程", score=0.60),
        _C("物流时效和海运清关的关系，影响备货节奏", score=0.66),
        _C("选品时市场容量与竞争强度的评估框架", score=0.50),
    ]
    out = rerank("退款话术怎么写", cand, top_k=2)
    assert "退款" in out[0].text, "重排失败：字面命中关键词的片段应排到第一"
    print("rerank 自测通过：top1 =", out[0].text[:20], "... | fused_score =", out[0].score)
    print("lexical_score('ACOS 优化', '广告 ACOS 过高优化') =",
          lexical_score("ACOS 优化", "广告 ACOS 过高优化投放预算"))
