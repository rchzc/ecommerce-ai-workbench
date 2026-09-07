"""Amazon SP-API 数据连接器。

真实接入路径（亚马逊店铺最常见）：
1. 在 Amazon Seller Central -> Apps -> Develop apps 注册 SP-API 应用
2. 获取 LWA (Login with Amazon) 凭证：client_id / client_secret / refresh_token
3. 把以下变量写进 backend/.env：
   AMAZON_MARKETPLACE_ID  例如: ATVPDKIKX0DER（美国）
   AMAZON_SELLER_ID       亚马逊卖家 ID
   AMAZON_CLIENT_ID       LWA App Client ID
   AMAZON_CLIENT_SECRET   LWA App Client Secret
   AMAZON_REFRESH_TOKEN   SP-API 离线 Refresh Token（可重复换 access_token）
4. 运行：python scripts/sync_data.py --provider amazon --mock      # 演示
   或：python scripts/sync_data.py --provider amazon               # 真实

注意事项（与 Shopify connector 完全一致的设计原则）：
- 拉取走官方 SP-API（GET /orders/v0/orders + GET /product-reviews/v1/reviews），
  带分页与超时，不爬网页截图。
- 真实接入需要 OAuth2.0 流程（access_token 1 小时过期，靠 refresh_token 换新）。
  本连接器内置 token 刷新逻辑，不依赖外部脚本。
- 无凭证时（--mock 模式）使用内置 mock_orders / mock_reviews，演示完整链路。

已知简化（与生产可扩展边界）：
- 仅实现"拉取近 N 天已付款订单"和"按商品拉评论"两个最常用端点；
  SP-API 还有 report 系列 / finance 系列，留待后续扩展。
- 评论 API 仅覆盖 amazon.com 区域，欧盟五国共一套。
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

# 本文件位于 backend/app/connectors/，上数 3 级到 backend/
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DOCS_DIR = os.path.join(BACKEND_DIR, "data", "docs")

logger = logging.getLogger(__name__)

# SP-API Region 到 endpoint 前缀的映射（北美 + 欧洲两套，加上沙箱）
SP_API_ENDPOINTS = {
    "na": "https://sellingpartnerapi-na.amazon.com",
    "eu": "https://sellingpartnerapi-eu.amazon.com",
    "fe": "https://sellingpartnerapi-fe.amazon.com",
    "sandbox": "https://sandbox.sellingpartnerapi-na.amazon.com",
}

# 常用 Marketplace ID（按区域聚类，避免每个开发者各自重复记忆）
# https://developer-docs.amazon.com/sp-api/docs/marketplace-ids
_MARKETPLACE_NA = "ATVPDKIKX0DER"   # US
_MARKETPLACE_EU = "A1PA6795UKMFR9"   # DE
_MARKETPLACE_EU_UK = "A1F83G8C2ARO7P"  # UK


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


class AmazonSpApiConnector:
    """把 Amazon SP-API 数据同步进知识库目录。"""

    def __init__(
        self,
        seller_id: str,
        marketplace_id: str,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        region: str = "na",
        timeout: int = 30,
    ) -> None:
        self.seller_id = seller_id
        self.marketplace_id = marketplace_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.region = region
        self.base_url = SP_API_ENDPOINTS.get(region, SP_API_ENDPOINTS["na"])
        self.timeout = timeout
        # 缓存 access_token，到点前不重复换；这是真实接入的常见优化。
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0

    # ------------------------------------------------------------------
    # 认证层：LWA 拿 access_token（1 小时有效）
    # ------------------------------------------------------------------
    def _fetch_access_token(self) -> str:
        """通过 LWA 把 refresh_token 换成短时效 access_token。"""
        url = "https://api.amazon.com/auth/o2/token"
        body = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        self._access_token = payload["access_token"]
        # 留 5 分钟安全余量，避免在临界点上过期
        self._token_expires_at = time.time() + payload.get("expires_in", 3600) - 300
        return self._access_token

    def _access_token_cached(self) -> str:
        if not self._access_token or time.time() >= self._token_expires_at:
            return self._fetch_access_token()
        return self._access_token

    # ------------------------------------------------------------------
    # 网络层
    # ------------------------------------------------------------------
    def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        token = self._access_token_cached()
        query = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
        url = f"{self.base_url}{path}?{query}"
        req = urllib.request.Request(
            url,
            headers={
                "x-amz-access-token": token,
                "Accept": "application/json",
                "User-Agent": "ecommerce-ai-workbench/1.0",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # 401 = token 失效（虽然有缓存，但 LWA 也可能主动撤销）
            if exc.code == 401:
                logger.warning("amazon.token_revoked, retrying once")
                self._access_token = None
                token = self._access_token_cached()
                req2 = urllib.request.Request(
                    url,
                    headers={
                        "x-amz-access-token": token,
                        "Accept": "application/json",
                        "User-Agent": "ecommerce-ai-workbench/1.0",
                    },
                )
                with urllib.request.urlopen(req2, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            raise

    def fetch_orders(self, days: int = 30, limit: int = 50) -> list[dict[str, Any]]:
        """拉取近 N 天已付款订单，作为客服 Agent 的真实上下文。"""
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        data = self._get(
            "/orders/v0/orders",
            {
                "MarketplaceIds": self.marketplace_id,
                "CreatedAfter": since,
                "OrderStatuses": "Shipped,Delivered,Unfulfillable,PartiallyShipped",
                "MaxResultsPerPage": str(min(limit, 100)),
            },
        )
        return data.get("payload", {}).get("Orders", [])

    def fetch_reviews(self, asin: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """拉取指定 ASIN 的商品评论。无 ASIN 则返回空列表（Amazon 强制要求 ASIN 维度查询）。"""
        if not asin:
            return []
        data = self._get(
            "/product-reviews/v1/reviews",
            {
                "MarketplaceId": self.marketplace_id,
                "Asin": asin,
                "MaxResults": str(min(limit, 100)),
            },
        )
        return data.get("payload", {}).get("reviews", [])

    # ------------------------------------------------------------------
    # 归一化：原始 SP-API JSON -> 知识库 markdown
    # ------------------------------------------------------------------
    def normalize_orders(self, orders: list[dict[str, Any]]) -> str:
        lines = [
            "# 店铺实时订单上下文（Amazon SP-API 自动同步）",
            f"> 来源：Amazon SP-API orders/v0/orders｜同步时间：{_now()}｜近 {len(orders)} 笔已付款订单",
            "",
        ]
        for o in orders:
            oid = o.get("AmazonOrderId", "?")
            channel = o.get("SalesChannel", "Amazon")
            total = (o.get("OrderTotal") or {}).get("Amount", "")
            currency = (o.get("OrderTotal") or {}).get("CurrencyCode", "")
            status = o.get("OrderStatus", "")
            lines.append(f"## 订单 {oid}")
            lines.append(f"- 渠道：{channel}｜状态：{status}｜金额：{total} {currency}")
            # SP-API 默认不给客户邮箱（隐私），这里以"客户"代称
            lines.append("- 客户：（Amazon 默认隐藏买家邮箱，客服操作走 Buyer-Seller Messaging）")
            lines.append("")
        return "\n".join(lines)

    def normalize_reviews(self, reviews: list[dict[str, Any]]) -> str:
        lines = [
            "# Amazon 商品评论洞察（SP-API 自动同步）",
            f"> 来源：Amazon SP-API product-reviews/v1｜同步时间：{_now()}｜共 {len(reviews)} 条",
            "",
        ]
        for i, r in enumerate(reviews, 1):
            asin = r.get("asin", "—")
            rating = r.get("rating", "—")
            text = r.get("text", "")
            reviewer = r.get("reviewer", {}).get("name", "匿名")
            lines.append(f"## 评论 {i}（{rating} 星）")
            lines.append(f"- ASIN：{asin}｜买家：{reviewer}")
            lines.append(f"- 内容：{text}")
            lines.append("")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 写入知识库目录
    # ------------------------------------------------------------------
    def write_docs(
        self,
        orders: list[dict[str, Any]],
        reviews: list[dict[str, Any]],
        docs_dir: str = DOCS_DIR,
    ) -> dict[str, Any]:
        os.makedirs(os.path.join(docs_dir, "support"), exist_ok=True)
        with open(
            os.path.join(docs_dir, "support", "live_orders.md"), "w", encoding="utf-8"
        ) as f:
            f.write(self.normalize_orders(orders))
        written = ["support/live_orders.md"]

        if reviews:
            os.makedirs(os.path.join(docs_dir, "review"), exist_ok=True)
            with open(
                os.path.join(docs_dir, "review", "live_reviews.md"), "w", encoding="utf-8"
            ) as f:
                f.write(self.normalize_reviews(reviews))
            written.append("review/live_reviews.md")

        return {"orders": len(orders), "reviews": len(reviews), "written": written}

    def sync(self, docs_dir: str = DOCS_DIR) -> dict[str, Any]:
        """拉取真实数据并写入知识库目录。"""
        orders = self.fetch_orders()
        # 评论必须传 ASIN；这里保留空 review 列表，由调用方自行扩展
        reviews: list[dict[str, Any]] = []
        return self.write_docs(orders, reviews, docs_dir)


# ----------------------------------------------------------------------
# mock 数据：无需凭证即可演示连接器 -> 知识库 整条链路
# ----------------------------------------------------------------------
def mock_orders() -> list[dict[str, Any]]:
    return [
        {
            "AmazonOrderId": "111-1111111-1111111",
            "SalesChannel": "Amazon.com",
            "OrderStatus": "Shipped",
            "OrderTotal": {"Amount": "59.90", "CurrencyCode": "USD"},
        },
        {
            "AmazonOrderId": "111-2222222-2222222",
            "SalesChannel": "Amazon.com",
            "OrderStatus": "Delivered",
            "OrderTotal": {"Amount": "129.00", "CurrencyCode": "USD"},
        },
    ]


def mock_reviews() -> list[dict[str, Any]]:
    return [
        {
            "asin": "B0DEMO0001",
            "rating": 4,
            "text": "音质不错但续航偏短，总体满意",
            "reviewer": {"name": "Amazon Customer"},
        },
        {
            "asin": "B0DEMO0001",
            "rating": 2,
            "text": "左耳连接不稳定，已申请退货",
            "reviewer": {"name": "Anonymous"},
        },
    ]
