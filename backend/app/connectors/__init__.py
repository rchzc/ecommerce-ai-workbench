"""数据连接器：把真实店铺数据接入知识层。

设计原则（面试可讲）：
- 连接器只负责「拉取 -> 清洗归一 -> 写入 data/docs/<domain>」。
- 重建向量库复用已有的 KnowledgeService.rebuild()，智能体与 RAG 零改动。
- 因此「接真实店铺数据」= 新增一个连接器模块，不动任何业务代码。

当前实现：
- Shopify（REST Admin API）+ 本地 mock 模式（无需凭证即可演示整条链路）。
- 评论来自 CSV 导出（Shopify 原生 Admin API 不含评论，符合真实情况，不伪造 API）。
"""
from .shopify import ShopifyConnector, mock_orders, mock_reviews

__all__ = ["ShopifyConnector", "mock_orders", "mock_reviews"]
