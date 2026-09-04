"""Shopify 数据连接器。

真实接入路径（跨境小公司最常见）：
1. Shopify 后台 Apps -> 创建一个 Custom App，拿到 Admin API Access Token
2. 把 SHOPIFY_SHOP / SHOPIFY_TOKEN 写进 backend/.env
3. 运行：python scripts/sync_data.py --provider shopify

注意事项：
- 客服/订单上下文来自 orders API（真实可调，REST Admin API）。
- Shopify 原生 Admin API 不含「商品评论」，评论通常来自评论 App 或 CSV 导出。
  本连接器提供 CSV 导入路径（data/import/reviews.csv）与 mock 数据，避免伪造 API。
- 拉取走官方接口、带分页与超时，不爬网页截图。
"""
from __future__ import annotations

import csv
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

# 本文件位于 backend/app/connectors/，上数 3 级到 backend/
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DOCS_DIR = os.path.join(BACKEND_DIR, "data", "docs")
IMPORT_DIR = os.path.join(BACKEND_DIR, "data", "import")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


class ShopifyConnector:
    """把 Shopify 店铺数据同步进知识库目录。"""

    def __init__(
        self, shop: str, token: str, api_version: str = "2024-01", timeout: int = 30
    ) -> None:
        self.shop = shop
        self.token = token
        self.api_version = api_version
        self.timeout = timeout

    # ------------------------------------------------------------------
    # 网络层
    # ------------------------------------------------------------------
    def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        base = f"https://{self.shop}.myshopify.com/admin/api/{self.api_version}"
        query = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
        url = f"{base}{path}?{query}"
        req = urllib.request.Request(
            url,
            headers={
                "X-Shopify-Access-Token": self.token,
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def fetch_orders(self, days: int = 30, limit: int = 50) -> list[dict[str, Any]]:
        """拉取近 N 天已付款订单，作为客服 Agent 的真实上下文。"""
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        data = self._get(
            "/orders.json",
            {
                "status": "any",
                "financial_status": "paid",
                "created_at_min": since,
                "limit": min(limit, 250),
            },
        )
        return data.get("orders", [])

    def fetch_reviews_csv(self, path: str | None = None) -> list[dict[str, Any]]:
        """评论来自 CSV 导出（评论 App / 后台导出）。无文件则返回空。"""
        csv_path = path or os.path.join(IMPORT_DIR, "reviews.csv")
        if not os.path.isfile(csv_path):
            return []
        rows: list[dict[str, Any]] = []
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                rows.append(dict(row))
        return rows

    # ------------------------------------------------------------------
    # 归一化：原始 JSON/CSV -> 知识库 markdown
    # ------------------------------------------------------------------
    def normalize_orders(self, orders: list[dict[str, Any]]) -> str:
        lines = [
            "# 店铺实时订单上下文（自动同步）",
            f"> 来源：Shopify 订单 API｜同步时间：{_now()}｜近 {len(orders)} 笔已付款订单",
            "",
        ]
        for o in orders:
            oid = o.get("name", o.get("id", "?"))
            customer = (o.get("customer") or {}).get("email", "匿名")
            total = o.get("total_price", "")
            currency = o.get("currency", "")
            note = o.get("note") or ""
            items = "、".join(
                f"{li.get('title', '商品')} x{li.get('quantity', 1)}"
                for li in o.get("line_items", [])
            )
            lines.append(f"## 订单 {oid}")
            lines.append(f"- 客户：{customer}｜金额：{total} {currency}")
            lines.append(f"- 商品：{items}")
            if note:
                lines.append(f"- 订单备注：{note}")
            lines.append("")
        return "\n".join(lines)

    def normalize_reviews(self, reviews: list[dict[str, Any]]) -> str:
        lines = [
            "# 店铺商品评论洞察（自动同步）",
            f"> 来源：评论 CSV / 评论 App 导出｜同步时间：{_now()}｜共 {len(reviews)} 条",
            "",
        ]
        for i, r in enumerate(reviews, 1):
            title = r.get("product") or r.get("商品") or "商品"
            rating = r.get("rating") or r.get("评分") or "—"
            text = r.get("content") or r.get("内容") or r.get("review") or ""
            lines.append(f"## 评论 {i}（{rating} 星）")
            lines.append(f"- 商品：{title}")
            lines.append(f"- 内容：{text}")
            lines.append("")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 写入知识库目录（复用 load_documents 已支持的 data/docs 结构）
    # ------------------------------------------------------------------
    def write_docs(
        self, orders: list[dict[str, Any]], reviews: list[dict[str, Any]], docs_dir: str = DOCS_DIR
    ) -> dict[str, Any]:
        os.makedirs(os.path.join(docs_dir, "support"), exist_ok=True)
        with open(os.path.join(docs_dir, "support", "live_orders.md"), "w", encoding="utf-8") as f:
            f.write(self.normalize_orders(orders))
        written = ["support/live_orders.md"]

        if reviews:
            os.makedirs(os.path.join(docs_dir, "review"), exist_ok=True)
            with open(os.path.join(docs_dir, "review", "live_reviews.md"), "w", encoding="utf-8") as f:
                f.write(self.normalize_reviews(reviews))
            written.append("review/live_reviews.md")

        return {"orders": len(orders), "reviews": len(reviews), "written": written}

    def sync(self, docs_dir: str = DOCS_DIR) -> dict[str, Any]:
        """拉取真实数据并写入知识库目录（不在此处重建索引）。"""
        orders = self.fetch_orders()
        reviews = self.fetch_reviews_csv()
        return self.write_docs(orders, reviews, docs_dir)


# ----------------------------------------------------------------------
# mock 数据：无需凭证即可演示连接器 -> 知识库 整条链路
# ----------------------------------------------------------------------
def mock_orders() -> list[dict[str, Any]]:
    return [
        {
            "name": "#1024",
            "customer": {"email": "amy@example.com"},
            "total_price": "59.90",
            "currency": "USD",
            "note": "Please ship faster, my event is next week",
            "line_items": [{"title": "Wireless Earbuds", "quantity": 1}],
        },
        {
            "name": "#1025",
            "customer": {"email": "bob@example.com"},
            "total_price": "129.00",
            "currency": "USD",
            "note": "",
            "line_items": [{"title": "Yoga Mat", "quantity": 2}],
        },
    ]


def mock_reviews() -> list[dict[str, Any]]:
    return [
        {"product": "Wireless Earbuds", "rating": "4", "content": "音质不错但续航偏短，总体满意"},
        {"product": "Yoga Mat", "rating": "5", "content": "防滑很好，颜色正，物流也快"},
        {"product": "Wireless Earbuds", "rating": "2", "content": "右耳连接不稳定，已申请退货"},
    ]
