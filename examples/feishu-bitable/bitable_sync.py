"""飞书多维表格 ↔ 跨境电商 AI 工作台：库存补货预警同步脚本。

闭环（三跳）：
    飞书多维表格（库存快照）
        → 本脚本筛出预警行
        → 调工作台 Webhook /api/hooks/agent/replenish（X-API-Key 鉴权）
        → 把 AI 建议回写表格的「补货建议 / 建议下单量 / 建议时间」

设计要点（面试可讲的四个点）：
1. **单行失败隔离**：某一行 SKU 调模型失败，只记录原因，不影响其余行 —— 与工作台
   CSV 批量任务同一套思路，一批数据里有一行脏数据不会整批失败。
2. **幂等**：已写过的行除非 --force，否则不再重复调用模型（省钱，也避免建议反复变）。
3. **--dry-run 先行**：默认只看不改，确认无误再 --apply。给外部系统写数据必须先演练。
4. **快速失败**：缺配置直接退出并说清缺哪个变量，不静默降级。

依赖：仅标准库（urllib），不需要 pip install。

用法：
    python bitable_sync.py --demo                 # 无飞书配置也能演示（用内置样例 SKU）
    python bitable_sync.py                        # dry-run：只打印将要回写的内容
    python bitable_sync.py --apply                # 真正回写飞书表格
    python bitable_sync.py --apply --force        # 覆盖已有建议
    python bitable_sync.py --threshold 20         # 自定义预警阈值（可用天数）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any, Iterable

FEISHU_BASE = "https://open.feishu.cn/open-apis"
TOKEN_PATH = "/auth/v3/tenant_access_token/internal"

# 表格列名。飞书里就是这些中文列名；想换名改这里（或设环境变量覆盖）
FIELD_SKU = os.getenv("BITABLE_FIELD_SKU", "SKU")
FIELD_ON_HAND = os.getenv("BITABLE_FIELD_ON_HAND", "在库")
FIELD_IN_TRANSIT = os.getenv("BITABLE_FIELD_IN_TRANSIT", "在途")
FIELD_DAILY_SALES = os.getenv("BITABLE_FIELD_DAILY_SALES", "日均销量")
FIELD_DAYS_LEFT = os.getenv("BITABLE_FIELD_DAYS_LEFT", "可用天数")
FIELD_ALERT = os.getenv("BITABLE_FIELD_ALERT", "预警状态")
FIELD_ADVICE = os.getenv("BITABLE_FIELD_ADVICE", "补货建议")
FIELD_QTY = os.getenv("BITABLE_FIELD_QTY", "建议下单量")
FIELD_UPDATED = os.getenv("BITABLE_FIELD_UPDATED", "建议时间")

# 演示数据：面试现场没配飞书也能跑通整条链路
# 三条分别演示：触发预警 / 触发预警（更紧急）/ 库存充足被阈值跳过
DEMO_ROWS: list[dict[str, Any]] = [
    {"record_id": "demo-1", "fields": {"SKU": "KB-8800", "在库": 320, "在途": 200, "日均销量": 40}},
    {"record_id": "demo-2", "fields": {"SKU": "MIC-Lavalier-Pro", "在库": 90, "在途": 0, "日均销量": 15}},
    {"record_id": "demo-3", "fields": {"SKU": "SOUND-CARD-X2", "在库": 1500, "在途": 0, "日均销量": 12}},
]


class ConfigError(RuntimeError):
    """配置缺失。与工作台一致：缺配置就明确报错，不做静默降级。"""


class FeishuError(RuntimeError):
    """飞书接口返回非 0 code，或网络层失败。"""


# ------------------------------------------------------------------ HTTP


def _request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """统一 HTTP：超时、非 2xx 与业务 code 都转成可读异常。"""
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json; charset=utf-8", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise FeishuError(f"HTTP {exc.code} {url}：{detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # 连接被拒/重置/超时，绝大多数是工作台没启动或端口不对
        raise FeishuError(
            f"连不上工作台 {url}：{exc}。"
            f"请确认已启动（双击 启动.bat，看到 http://127.0.0.1:8000），"
            f"或检查 WORKBENCH_BASE_URL 是否与实际端口一致。"
        ) from exc

    if isinstance(payload, dict) and payload.get("code") not in (0, None):
        raise FeishuError(f"飞书返回错误 code={payload.get('code')}：{payload.get('msg')}")
    return payload


# ------------------------------------------------------------------ 飞书


def get_tenant_token(app_id: str, app_secret: str) -> str:
    payload = _request(
        "POST",
        f"{FEISHU_BASE}{TOKEN_PATH}",
        body={"app_id": app_id, "app_secret": app_secret},
        timeout=15,
    )
    return payload["tenant_access_token"]


def list_records(token: str, app_token: str, table_id: str) -> list[dict[str, Any]]:
    """拉取全部记录（自动翻页）。"""
    records: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        url = f"{FEISHU_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/records?page_size=200"
        if page_token:
            url += f"&page_token={page_token}"
        payload = _request("GET", url, headers={"Authorization": f"Bearer {token}"})
        data = payload.get("data") or {}
        records.extend(data.get("items") or [])
        if not data.get("has_more"):
            break
        page_token = data.get("page_token")
        if not page_token:
            break
    return records


def batch_update(
    token: str, app_token: str, table_id: str, updates: list[dict[str, Any]]
) -> None:
    """批量回写。飞书单批上限 500 条。"""
    for i in range(0, len(updates), 500):
        chunk = updates[i : i + 500]
        _request(
            "POST",
            f"{FEISHU_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_update",
            headers={"Authorization": f"Bearer {token}"},
            body={"records": chunk},
        )


# ------------------------------------------------------------------ 业务判定


def _num(value: Any) -> float:
    """表格里的数字可能是数字、字符串或空，统一成 float，脏值当 0。"""
    if isinstance(value, bool) or value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return 0.0


def days_left(fields: dict[str, Any]) -> float:
    """可用天数 = (在库 + 在途) / 日均销量。表格已有公式列时优先用公式值。"""
    if FIELD_DAYS_LEFT in fields and _num(fields[FIELD_DAYS_LEFT]) > 0:
        return _num(fields[FIELD_DAYS_LEFT])
    daily = _num(fields.get(FIELD_DAILY_SALES))
    if daily <= 0:
        return float("inf")  # 没有销量数据，不判预警，交给人工
    return (_num(fields.get(FIELD_ON_HAND)) + _num(fields.get(FIELD_IN_TRANSIT))) / daily


def needs_advice(fields: dict[str, Any], threshold: float, force: bool) -> bool:
    if not force and str(fields.get(FIELD_ADVICE) or "").strip():
        return False  # 幂等：已有建议且未 --force 就跳过，不重复烧 token
    if "紧急" in str(fields.get(FIELD_ALERT) or "") or "补货" in str(fields.get(FIELD_ALERT) or ""):
        return True
    return days_left(fields) < threshold


def call_workbench(base: str, api_key: str, row: dict[str, Any], timeout: int) -> dict[str, Any]:
    """调工作台 Webhook 拿补货建议。返回的 data 已是结构化 JSON。"""
    fields = row["fields"]
    left = days_left(fields)
    payload = {
        "sku": fields.get(FIELD_SKU),
        "on_hand": _num(fields.get(FIELD_ON_HAND)),
        "in_transit": _num(fields.get(FIELD_IN_TRANSIT)),
        "daily_sales": _num(fields.get(FIELD_DAILY_SALES)),
        # 无销量数据时不传 Infinity（不是合法 JSON），用 -1 表示"未知"
        "days_left": round(left, 1) if left != float("inf") else -1,
        "alert": fields.get(FIELD_ALERT),
        "season": os.getenv("REPLENISH_SEASON", "常规"),
    }
    result = _request(
        "POST",
        f"{base.rstrip('/')}/api/hooks/agent/replenish",
        headers={"X-API-Key": api_key},
        body={"payload": payload},
        timeout=timeout,
    )
    return result.get("data") or {}


# ------------------------------------------------------------------ 主流程


def render(rows: Iterable[tuple[dict[str, Any], dict[str, Any] | str]]) -> None:
    print(f"{'SKU':<20}{'可用天数':>9}{'紧急度':<12}{'建议量':>8}  摘要")
    print("-" * 96)
    for row, advice in rows:
        fields = row["fields"]
        if isinstance(advice, str):  # 失败行
            print(f"{str(fields.get(FIELD_SKU)):<20}{days_left(fields):>9.1f}{'调用失败':<12}{'-':>8}  {advice[:50]}")
            continue
        print(
            f"{str(fields.get(FIELD_SKU)):<20}{days_left(fields):>9.1f}"
            f"{str(advice.get('urgency', '-')):<12}{str(advice.get('suggested_qty', '-')):>8}  "
            f"{str(advice.get('summary', ''))[:50]}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="飞书多维表格库存补货预警同步")
    parser.add_argument("--dry-run", action="store_true", help="只看不改（默认行为）")
    parser.add_argument("--apply", action="store_true", help="真正回写飞书表格")
    parser.add_argument("--force", action="store_true", help="覆盖已有建议（默认跳过已填行）")
    parser.add_argument("--threshold", type=float, default=15.0, help="可用天数预警阈值，默认 15")
    parser.add_argument("--timeout", type=int, default=180, help="单次模型调用超时（秒）")
    parser.add_argument("--demo", action="store_true", help="用内置样例数据演示，不访问飞书")
    args = parser.parse_args()

    base = os.getenv("WORKBENCH_BASE_URL", "http://127.0.0.1:8000")
    api_key = os.getenv("WORKFLOW_API_KEY", "")
    if not api_key:
        raise ConfigError(
            "缺少 WORKFLOW_API_KEY。请在 backend/.env 配置后重启服务，"
            "或在本脚本运行环境里 export WORKFLOW_API_KEY=...（Webhook 不做无鉴权放行）。"
        )

    if args.demo:
        if args.apply:
            raise ConfigError("--demo 只用于演示，不能回写（没有飞书配置）。去掉 --apply，或配置飞书后去掉 --demo。")
        rows = DEMO_ROWS
        print("[demo 模式] 使用内置样例 SKU，不访问飞书表格\n")
    else:
        app_id = os.getenv("FEISHU_APP_ID", "")
        app_secret = os.getenv("FEISHU_APP_SECRET", "")
        app_token = os.getenv("FEISHU_BITABLE_APP_TOKEN", "")
        table_id = os.getenv("FEISHU_BITABLE_TABLE_ID", "")
        missing = [
            name
            for name, value in (
                ("FEISHU_APP_ID", app_id),
                ("FEISHU_APP_SECRET", app_secret),
                ("FEISHU_BITABLE_APP_TOKEN", app_token),
                ("FEISHU_BITABLE_TABLE_ID", table_id),
            )
            if not value
        ]
        if missing:
            raise ConfigError(f"缺少飞书配置：{', '.join(missing)}（在飞书开放平台建企业自建应用并开通多维表格权限）")
        token = get_tenant_token(app_id, app_secret)
        rows = list_records(token, app_token, table_id)
        print(f"已拉取 {len(rows)} 条记录\n")

    targets = [r for r in rows if needs_advice(r["fields"], args.threshold, args.force)]
    if not targets:
        print(f"没有需要处理的行（阈值 {args.threshold} 天，已有建议的行默认跳过）。")
        return 0
    print(f"命中预警 {len(targets)} 行，开始调用工作台补货 Agent…\n")

    started = time.perf_counter()
    updates: list[dict[str, Any]] = []
    preview: list[tuple[dict[str, Any], dict[str, Any] | str]] = []
    failed = 0

    for row in targets:
        try:
            advice = call_workbench(base, api_key, row, args.timeout)
        except (FeishuError, ConfigError) as exc:
            # 单行失败隔离：一行 SKU 失败不中断整批
            failed += 1
            preview.append((row, str(exc)))
            continue
        preview.append((row, advice))
        updates.append(
            {
                "record_id": row["record_id"],
                "fields": {
                    FIELD_ADVICE: advice.get("summary") or advice.get("reason") or "",
                    FIELD_QTY: advice.get("suggested_qty", 0),
                    FIELD_UPDATED: datetime.now().strftime("%Y-%m-%d %H:%M"),
                },
            }
        )

    render(preview)

    elapsed = round(time.perf_counter() - started, 1)
    print(f"\n耗时 {elapsed}s｜成功 {len(updates)} 行｜失败 {failed} 行")

    if args.demo or not args.apply:
        print("（dry-run：未回写。加 --apply 真正写入飞书表格）")
        return 0

    token = get_tenant_token(os.environ["FEISHU_APP_ID"], os.environ["FEISHU_APP_SECRET"])
    batch_update(token, os.environ["FEISHU_BITABLE_APP_TOKEN"], os.environ["FEISHU_BITABLE_TABLE_ID"], updates)
    print(f"已回写 {len(updates)} 行到飞书多维表格")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ConfigError, FeishuError) as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        sys.exit(2)
