"""飞书多维表格（Bitable）连接器：把日报/指标行写入在线表格。

真实接入路径：
1. 飞书开放平台 https://open.feishu.cn 创建企业自建应用
2. 开通「多维表格」权限（bitable:app），并把应用添加为目标多维表格的协作者
3. 把以下变量写进 backend/.env：
   FEISHU_APP_ID           自建应用 App ID
   FEISHU_APP_SECRET       自建应用 App Secret
   FEISHU_BITABLE_APP_TOKEN  多维表格 app_token（分享链接里 ?p= 后那段）
   FEISHU_TABLE_ID         数据表 table_id（tbl 开头）
4. 目标表需包含字段：日期(文本)、GMV(数字)、订单数(数字)、销量(数字)、
   预警数(数字)、预警摘要(文本)、日报(文本)

设计原则（与 Shopify / Amazon 连接器一致）：
- 只用标准库 urllib，不引入新依赖；带超时，不爬网页。
- tenant_access_token 缓存到过期前 5 分钟，不每次都换。
- 无凭证时 mock 模式演示完整链路：格式化字段 -> 模拟写入 -> 返回行数据，
  让"日报 -> 多维表格"这条集成路径无需真实凭证也能讲清楚。

已知简化（与生产可扩展边界）：
- 只实现 batch_create（日报是追加写场景，足够）；
  查询/更新/删除记录留待后续扩展。
- 限流退避只做单次 429 提示，未做指数退避重试（日报每天一次，撞限流概率极低）。
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

BASE_URL = "https://open.feishu.cn/open-apis"

# 日报写入多维表格的字段清单（顺序即表格列建议顺序）
REPORT_FIELDS = ("日期", "GMV", "订单数", "销量", "预警数", "预警摘要", "日报")


class FeishuBitableConnector:
    """飞书多维表格写入连接器。"""

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        bitable_app_token: str,
        table_id: str,
        timeout: int = 30,
    ) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.bitable_app_token = bitable_app_token
        self.table_id = table_id
        self.timeout = timeout
        # tenant_access_token 约 2 小时有效，缓存到过期前 5 分钟
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    # ------------------------------------------------------------------
    # 认证层
    # ------------------------------------------------------------------
    def _fetch_tenant_token(self) -> str:
        url = f"{BASE_URL}/auth/v3/tenant_access_token/internal"
        body = json.dumps({"app_id": self.app_id, "app_secret": self.app_secret}).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}
        )
        data = self._read_json(req)
        # 飞书业务失败时 code != 0 且不带 token
        if data.get("code") != 0 or not data.get("tenant_access_token"):
            raise ValueError(
                f"获取 tenant_access_token 失败：code={data.get('code')} msg={data.get('msg')}"
            )
        self._token = data["tenant_access_token"]
        self._token_expires_at = time.time() + data.get("expire", 7200) - 300
        return self._token

    def _tenant_token(self) -> str:
        if not self._token or time.time() >= self._token_expires_at:
            return self._fetch_tenant_token()
        return self._token

    # ------------------------------------------------------------------
    # 网络层
    # ------------------------------------------------------------------
    def _read_json(self, req: urllib.request.Request) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")[:300]
            except Exception:  # noqa: BLE001 - 读不出响应体时只保留状态码
                pass
            raise ConnectionError(f"飞书 API HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise ConnectionError(f"飞书 API 网络错误：{exc.reason}") from exc

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{BASE_URL}{path}"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._tenant_token()}",
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "ecommerce-ai-workbench/1.0",
            },
        )
        data = self._read_json(req)
        if data.get("code") != 0:
            raise ValueError(f"飞书 API 业务错误：code={data.get('code')} msg={data.get('msg')}")
        return data.get("data", {})

    # ------------------------------------------------------------------
    # 业务层
    # ------------------------------------------------------------------
    def batch_create(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """批量追加记录。fields 的键需与多维表格列名完全一致。"""
        if not rows:
            return {"created": 0}
        payload = {"records": [{"fields": row} for row in rows]}
        data = self._post(
            f"/bitable/v1/apps/{self.bitable_app_token}/tables/{self.table_id}/records/batch_create",
            payload,
        )
        records = data.get("records", [])
        return {"created": len(records)}

    def push_daily_report(self, report: dict[str, Any]) -> dict[str, Any]:
        """把日报 dict 转成表格行并写入。日报结构见 services/report_service.py。"""
        row = format_report_row(report)
        return self.batch_create([row])


# ----------------------------------------------------------------------
# 纯函数：日报 dict -> 多维表格 fields 行（mock 与真实路径共用，便于单测）
# ----------------------------------------------------------------------
def format_report_row(report: dict[str, Any]) -> dict[str, Any]:
    """日报聚合结果 -> 飞书多维表格一行 fields。

    放在模块级而不是类方法：不碰网络，单测无需 mock 连接器。
    """
    metrics = report.get("metrics", {})
    alerts = report.get("alerts", [])
    alert_summary = "；".join(a.get("message", "") for a in alerts[:5]) or "无"
    # 多维表格单字段有长度上限，日报 markdown 截断到 5000 字符
    markdown = (report.get("markdown") or "")[:5000]
    return {
        "日期": str(report.get("date", "")),
        "GMV": round(float(metrics.get("revenue", 0)), 2),
        "订单数": int(metrics.get("orders", 0)),
        "销量": int(metrics.get("units", 0)),
        "预警数": len(alerts),
        "预警摘要": alert_summary,
        "日报": markdown,
    }


# ----------------------------------------------------------------------
# mock 模式：无需真实凭证即可演示「日报 -> 多维表格」链路
# ----------------------------------------------------------------------
class MockFeishuBitableConnector(FeishuBitableConnector):
    """不发起任何网络请求的演示实现，接口与真实连接器完全一致。"""

    def __init__(self) -> None:
        super().__init__("mock", "mock", "mock", "mock")

    def _tenant_token(self) -> str:  # noqa: D102 - 覆写为不发请求
        return "mock-token"

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:  # noqa: D102
        records = payload.get("records", [])
        logger.info("feishu.mock_write", extra={"path": path, "rows": len(records)})
        return {"records": [{"record_id": f"mock{i + 1}", "fields": r["fields"]} for i, r in enumerate(records)]}
