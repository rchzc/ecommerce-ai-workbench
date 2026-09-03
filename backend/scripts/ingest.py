"""知识库构建脚本。

两种用法：
    python scripts/ingest.py          # 命令行
    POST /api/kb/rebuild              # HTTP（无需登录服务器）

流程：扫描 data/docs → 按域切分 → 批量向量化 → 写入 ChromaDB。
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

# 让脚本既能被「包导入」也能被「直接运行」
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import Settings, load_settings  # noqa: E402
from app.core.embeddings import Embedder  # noqa: E402
from app.core.rag import chunk_document, load_documents  # noqa: E402
from app.core.vectorstore import VectorStore  # noqa: E402

DOCS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "docs")
PERSIST_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "chroma_db")

# 每批向量化的文本数。太大容易触发限流，太小会显著变慢。
BATCH_SIZE = 16


async def rebuild_knowledge_base(
    settings: Settings,
    store: VectorStore,
    embedder: Embedder,
    docs_dir: str = DOCS_DIR,
) -> dict:
    """重建向量索引，返回统计信息。"""
    started = time.perf_counter()

    documents = load_documents(docs_dir)
    if not documents:
        raise RuntimeError(f"未在 {docs_dir} 下找到任何 .md 文档")

    all_chunks = []
    for domain, filename, content in documents:
        chunks = chunk_document(
            content,
            source=filename,
            domain=domain,
            chunk_size=settings.chunk_size,
            overlap=settings.chunk_overlap,
        )
        all_chunks.extend(chunks)

    store.reset()

    # 分批向量化，避免单次请求体过大触发厂商限流
    total = len(all_chunks)
    for start in range(0, total, BATCH_SIZE):
        batch = all_chunks[start : start + BATCH_SIZE]
        texts = [c.text for c in batch]
        vectors = await embedder.embed(texts)
        store.add(
            ids=[c.chunk_id for c in batch],
            texts=texts,
            metadatas=[
                {"source": c.source, "domain": c.domain, "index": c.index}
                for c in batch
            ],
            embeddings=vectors if vectors else None,
        )
        done = min(start + BATCH_SIZE, total)
        print(f"  已写入 {done}/{total} 切片", flush=True)

    elapsed = round((time.perf_counter() - started) * 1000, 2)
    return {
        "chunks": total,
        "files": len(documents),
        "embedding_mode": embedder.mode,
        "elapsed_ms": elapsed,
    }


async def _main() -> int:
    try:
        settings = load_settings()
    except Exception as exc:
        print(f"[配置错误] {exc}")
        return 1

    store = VectorStore(settings, PERSIST_DIR)
    embedder = Embedder(settings)

    print(f"厂商：{settings.provider_label}")
    print(f"向量化：{embedder.mode}")
    print(f"文档目录：{DOCS_DIR}")
    print("开始构建知识库...")

    result = await rebuild_knowledge_base(settings, store, embedder)
    print(
        f"\n完成：{result['files']} 篇文档 → {result['chunks']} 个语义切片，"
        f"耗时 {result['elapsed_ms']} ms"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
