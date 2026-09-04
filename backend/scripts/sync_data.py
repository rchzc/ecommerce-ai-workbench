"""数据同步 CLI：把真实店铺数据接入知识库。

用法：
  # 演示（无需凭证）：生成 mock 数据写入知识库目录，并重建索引
  python scripts/sync_data.py --provider shopify --mock

  # 真实接入：需要 backend/.env 里的 SHOPIFY_SHOP / SHOPIFY_TOKEN
  python scripts/sync_data.py --provider shopify

  # 仅写文件、不重建索引（调试连接器用）
  python scripts/sync_data.py --provider shopify --mock --no-rebuild

连接器只负责「拉取 -> 写 data/docs」，重建索引复用 KnowledgeService.rebuild()。
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

# 让脚本能 import backend 包（脚本位于 backend/scripts/）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import load_settings  # noqa: E402
from app.connectors import ShopifyConnector, mock_orders, mock_reviews  # noqa: E402
from app.core.embeddings import Embedder  # noqa: E402
from app.core.vectorstore import VectorStore  # noqa: E402
from app.services.agent_service import KnowledgeService  # noqa: E402

# backend/ 目录（与 agent_service 中 DOCS_DIR 的解析保持一致）
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHROMA_DIR = os.getenv("CHROMA_DIR") or os.path.join(BACKEND_DIR, "chroma_db")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="把真实店铺数据同步进知识库")
    p.add_argument("--provider", default="shopify", choices=["shopify"])
    p.add_argument("--mock", action="store_true", help="使用内置 mock 数据，无需凭证")
    p.add_argument("--no-rebuild", action="store_true", help="只写文件，不重建向量索引")
    return p.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args()

    if args.mock:
        connector = ShopifyConnector("mock", "mock")
        result = connector.write_docs(mock_orders(), mock_reviews())
        print(f"[mock] 已写入：{result['written']}（订单 {result['orders']} / 评论 {result['reviews']}）")
    else:
        shop = os.getenv("SHOPIFY_SHOP")
        token = os.getenv("SHOPIFY_TOKEN")
        if not (shop and token):
            print("缺少 SHOPIFY_SHOP / SHOPIFY_TOKEN，请用 --mock 演示，或在 backend/.env 填写后重试")
            sys.exit(2)
        connector = ShopifyConnector(shop, token)
        result = connector.sync()
        print(f"[shopify] 已同步订单 {result['orders']} 条、评论 {result['reviews']} 条 -> {result['written']}")

    if args.no_rebuild:
        print("跳过重建索引（--no-rebuild）。需要生效时运行：curl -X POST http://127.0.0.1:8000/api/kb/rebuild")
        return

    # 重建向量索引：复用与 HTTP 接口完全相同的实现
    settings = load_settings()
    store = VectorStore(settings, CHROMA_DIR)
    embedder = Embedder(settings)
    ks = KnowledgeService(store=store, embedder=embedder, settings=settings)
    summary = asyncio.run(ks.rebuild())
    print(f"[kb] 重建完成：{summary['chunks']} 切片 / {summary['files']} 文件")


if __name__ == "__main__":
    main()
