"""数据连接器：把真实店铺数据接入知识层。

设计原则（面试可讲）：
- 连接器只负责「拉取 -> 清洗归一 -> 写入 data/docs/<domain>」。
- 重建向量库复用已有的 KnowledgeService.rebuild()，智能体与 RAG 零改动。
- 因此「接真实店铺数据」= 新增一个连接器模块，不动任何业务代码。

当前实现：
- Shopify（REST Admin API）+ 本地 mock 模式（无需凭证即可演示整条链路）。
  评论来自 CSV 导出（Shopify 原生 Admin API 不含评论，符合真实情况，不伪造 API）。
- Amazon SP-API（orders / product-reviews 双端点）+ LWA OAuth2 access_token 缓存 + mock。
- 飞书多维表格（Bitable batch_create）+ tenant_access_token 缓存 + mock，
  作为日报/指标的「数据出口」—— 前两个连接器是数据入口，飞书是数据出口，
  出入口都齐了才叫「系统间数据打通」。

新增连接器的标准步骤：
  1. 在 connectors/ 下新建 <provider>.py，实现 Connector 类 + mock_orders / mock_reviews
  2. 在本 __init__.py 注册 export
  3. 在 scripts/sync_data.py 的 argparse choices 里加 <provider>
"""
from .shopify import ShopifyConnector, mock_orders, mock_reviews
from .amazon_sp_api import (
    AmazonSpApiConnector,
    mock_orders as mock_orders_amazon,
    mock_reviews as mock_reviews_amazon,
)
from .feishu_bitable import FeishuBitableConnector, MockFeishuBitableConnector, format_report_row

__all__ = [
    "ShopifyConnector",
    "mock_orders",
    "mock_reviews",
    "AmazonSpApiConnector",
    "mock_orders_amazon",
    "mock_reviews_amazon",
    "FeishuBitableConnector",
    "MockFeishuBitableConnector",
    "format_report_row",
]
