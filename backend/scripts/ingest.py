"""知识库构建命令行入口。

两种入口共用服务层的同一份实现，行为完全一致：
    python scripts/ingest.py     # 命令行
    POST /api/kb/rebuild         # HTTP（无需登录服务器）

这里只负责「拼装依赖 + 打印结果」，真正的重建逻辑在
app/services/agent_service.py 的 KnowledgeService.rebuild()。
"""
from __future__ import annotations

import asyncio
import os
import sys

# 让脚本既能被「包导入」也能被「直接运行」
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.config import load_settings  # noqa: E402
from app.core.embeddings import Embedder  # noqa: E402
from app.core.vectorstore import VectorStore  # noqa: E402
from app.services.agent_service import DOCS_DIR, KnowledgeService  # noqa: E402

PERSIST_DIR = os.getenv("CHROMA_DIR") or os.path.join(BACKEND_DIR, "chroma_db")


async def _main() -> int:
    try:
        settings = load_settings()
    except Exception as exc:
        print(f"[配置错误] {exc}")
        return 1

    store = VectorStore(settings, PERSIST_DIR)
    embedder = Embedder(settings)
    service = KnowledgeService(
        store=store, embedder=embedder, settings=settings, docs_dir=DOCS_DIR
    )

    print(f"厂商：{settings.provider_label}")
    print(f"向量化：{embedder.mode}")
    print(f"文档目录：{DOCS_DIR}")
    print("开始构建知识库...")

    try:
        result = await service.rebuild()
    except Exception as exc:
        print(f"[构建失败] {exc}")
        return 1

    print(
        f"\n完成：{result['files']} 篇文档 → {result['chunks']} 个语义切片，"
        f"耗时 {result['elapsed_ms']} ms"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
