"""RAG 检索质量评测：让"检索准"有数据，而非凭感觉。

两类检查：
1) 离线语料覆盖检查（默认，永远可跑、不联网、不需 chromadb）：
   把 21 篇文档按现有切分策略切块，对每个评测问题在「正确业务域」内用
   词面重叠排序，校验期望的关键词是否落在 top_k。证明知识库"覆盖"了这些问题，
   且按词面排序能把对应知识捞到前面。
2) 在线召回检查（--online，需联网 + LLM Key）：
   向量化问题 → 检索 → rerank 重排 → 校验期望关键词是否命中 top_k，
   输出 Recall@k。证明真实向量检索能把知识捞出来。

用法：
    python scripts/eval_retrieval.py            # 仅离线语料覆盖
    python scripts/eval_retrieval.py --online    # 离线 + 在线（需 LLM Key）

注：离线路径只依赖 app.core.rag 与 app.core.rerank，刻意不加载 vectorstore，
因此不需要 chromadb，可在无网络的沙箱直接跑出数字。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

# 离线路径依赖（不触发 chromadb）
from app.core.rag import chunk_document, load_documents  # noqa: E402
from app.core.rerank import lexical_score, rerank  # noqa: E402

# 与 app.config 默认值保持一致，离线评测不读 .env
CHUNK_SIZE = 600
CHUNK_OVERLAP = 50
TOP_K = 4
DOCS_DIR = os.path.join(BACKEND_DIR, "data", "docs")


# ---------------------------------------------------------------------------
# 评测集：覆盖 6 个业务域，must_contain 为期望命中的关键词（已确认存在于语料）。
# ---------------------------------------------------------------------------
EVAL_QUERIES = [
    {
        "domain": "selection",
        "question": "如何判断一个新品是否值得进入美国市场？",
        "must_contain": ["选品", "竞争", "市场", "进入建议"],
    },
    {
        "domain": "listing",
        "question": "怎么写高转化的亚马逊标题和五点描述？",
        "must_contain": ["标题", "五点", "关键词", "Listing"],
    },
    {
        "domain": "review",
        "question": "怎么分析买家评论找出产品痛点？",
        "must_contain": ["评论", "情感", "痛点", "差评"],
    },
    {
        "domain": "ads",
        "question": "ACOS 过高该怎么优化广告投放？",
        "must_contain": ["ACOS", "广告", "转化", "预算"],
    },
    {
        "domain": "logistics",
        "question": "发美国用海运还是空运更划算？",
        "must_contain": ["物流", "海运", "空运", "时效"],
    },
    {
        "domain": "support",
        "question": "客户要退款该怎么礼貌回复？",
        "must_contain": ["客服", "退款", "话术", "升级"],
    },
]


def _build_corpus() -> dict[str, list[str]]:
    """按域切块，返回 {domain: [chunk_text, ...]}。"""
    corpus: dict[str, list[str]] = {}
    for domain, _name, content in load_documents(DOCS_DIR):
        chunks = chunk_document(
            content,
            source=_name,
            domain=domain,
            chunk_size=CHUNK_SIZE,
            overlap=CHUNK_OVERLAP,
        )
        corpus.setdefault(domain, []).extend(c.text for c in chunks)
    return corpus


def _hit(query: str, chunks: list[str], must_contain: list[str], top_k: int) -> tuple[bool, int]:
    """在给定 chunk 列表里按词面分排序，返回 (是否命中 must_contain, top1 排名)。"""
    scored = sorted(
        ((lexical_score(query, c), c) for c in chunks),
        key=lambda x: x[0],
        reverse=True,
    )
    top = scored[:top_k]
    hit = any(any(kw in c for kw in must_contain) for _, c in top)
    return hit, len(top)


def run_offline(top_k: int = TOP_K) -> float:
    print("=" * 64)
    print("离线语料覆盖检查（词面排序，不联网）")
    print("=" * 64)
    corpus = _build_corpus()
    total_chunks = sum(len(v) for v in corpus.values())
    print(f"语料：{len(corpus)} 个域，共 {total_chunks} 个切片\n")

    hits = 0
    for item in EVAL_QUERIES:
        domain = item["domain"]
        pool = corpus.get(domain, [])
        ok, _ = _hit(item["question"], pool, item["must_contain"], top_k)
        hits += 1 if ok else 0
        status = "✅" if ok else "❌"
        print(f"{status} [{domain:9s}] {item['question']}  (域切片数={len(pool)})")

    coverage = hits / len(EVAL_QUERIES)
    print("-" * 64)
    print(f"离线覆盖：{hits}/{len(EVAL_QUERIES)} 个问题的期望知识落在 top{top_k}（{coverage:.0%}）")
    print("说明：这是在正确业务域内、用关键词排序验证知识可达性，不等同端到端回答准确率。")
    return coverage


async def run_online(top_k: int = TOP_K) -> float:
    print("\n" + "=" * 64)
    print("在线召回检查（真实向量检索 + rerank，需联网 + LLM Key）")
    print("=" * 64)
    # 懒加载：只有在线模式才需要 chromadb / 网络
    from app.config import load_settings  # noqa: E402
    from app.core.embeddings import Embedder  # noqa: E402
    from app.core.vectorstore import VectorStore  # noqa: E402

    try:
        settings = load_settings()
    except Exception as exc:  # noqa: BLE001
        print(f"[跳过] 无法加载配置：{exc}")
        return 0.0

    embedder = Embedder(settings)
    store = VectorStore(settings, os.getenv("CHROMA_DIR") or os.path.join(BACKEND_DIR, "chroma_db"))

    if store.count() == 0:
        print("[跳过] 知识库为空，请先 POST /api/kb/rebuild 或 python scripts/ingest.py")
        return 0.0

    hits = 0
    for item in EVAL_QUERIES:
        try:
            vectors = await embedder.embed([item["question"]])
        except Exception as exc:  # noqa: BLE001
            print(f"[跳过] 向量化失败（无 Key / 无网络）：{exc}")
            return 0.0
        embedding = vectors[0] if vectors else None
        try:
            candidates = store.query(embedding, domain=item["domain"], top_k=top_k * 2)
        except Exception as exc:  # noqa: BLE001
            print(f"[跳过] 检索失败：{exc}")
            return 0.0
        ranked = rerank(item["question"], candidates, top_k=top_k)
        ok = any(any(kw in c.text for kw in item["must_contain"]) for c in ranked)
        hits += 1 if ok else 0
        status = "✅" if ok else "❌"
        top_src = ranked[0].source if ranked else "-"
        print(f"{status} [{item['domain']:9s}] 命中知识来自：{top_src}")

    recall = hits / len(EVAL_QUERIES)
    print("-" * 64)
    print(f"在线 Recall@{top_k}：{hits}/{len(EVAL_QUERIES)}（{recall:.0%}）")
    return recall


async def _main() -> int:
    parser = argparse.ArgumentParser(description="RAG 检索质量评测")
    parser.add_argument("--online", action="store_true", help="额外跑在线向量召回检查（需 Key/网络）")
    parser.add_argument("--top-k", type=int, default=TOP_K, help="检索 top_k（默认 4）")
    args = parser.parse_args()

    run_offline(args.top_k)
    if args.online:
        await run_online(args.top_k)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
