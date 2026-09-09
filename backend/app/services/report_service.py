"""销售日报服务：多源 CSV 数据 -> 指标聚合 + 规则预警 + Markdown 日报。

这是「数据决策闭环」的最后一环（面试叙事可讲）：
    连接器拉数（入口）-> Agent 分析 -> 日报聚合 + 预警 -> 飞书多维表格（出口）

三个关键工程决策：
1. **规则预警不走大模型**。
   库存低于安全水位、销量环比骤降、差评率超标这类判断是确定性规则 ——
   用规则引擎做，可单测、可解释、零成本；大模型只该处理规则覆盖不了的
   模糊判断。每天几百次模型调用只为算一个「库存够不够」，那是在烧钱。
2. **数据源是标准 CSV 而不是直连数据库**。
   运营手里的数据天然来自各平台导出（Amazon 后台 / Shopify / ERP 都是 CSV），
   按统一列约定放进 data/sales/ 即可出日报 —— 换一个数据源只是换一份 CSV，
   服务零改动。后续接入 SP-API report 下载后，落盘成同格式即可无缝切换。
3. **日报同时产出结构化 dict 和 Markdown 文本**。
   dict 给前端看板渲染，Markdown 给飞书表格与邮件推送 —— 一份数据两种出口，
   不在展示层做二次聚合。

预警规则（全部可配置阈值，见 RULES）：
- 库存预警：SKU 当前库存 < 安全库存
- 销量骤降：当日 GMV 环比前一日跌幅 > 30%（按店铺维度）
- 差评率预警：SKU 当日差评率 > 10%（差评数/评论数）
- 广告占比预警：店铺广告花费 / GMV > 30%
"""
from __future__ import annotations

import csv
import logging
import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from ..connectors.feishu_bitable import FeishuBitableConnector, MockFeishuBitableConnector
from ..config import Settings
from ..errors import ConfigError, ExternalApiError, NotFoundError, ValidationError

logger = logging.getLogger(__name__)

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPORT_DIR = os.path.join(BACKEND_DIR, "data", "reports")

# 销售明细 CSV 的列约定（运营从各平台导出后按此格式落盘即可）
REQUIRED_COLUMNS = {"date", "shop", "sku", "orders", "units", "revenue"}
OPTIONAL_COLUMNS = {"ad_spend", "stock", "safe_stock", "reviews", "bad_reviews"}

# 预警阈值（集中一处，调参不改逻辑）
RULES = {
    "revenue_drop_ratio": 0.30,   # GMV 环比跌幅超过 30% 触发
    "bad_review_ratio": 0.10,     # 差评率超过 10% 触发
    "ad_spend_ratio": 0.30,       # 广告花费占 GMV 超过 30% 触发
}

# 预警级别 -> 前端配色语义
LEVEL_HIGH = "high"
LEVEL_MID = "mid"


@dataclass
class DailyReport:
    """日报聚合结果。metrics 给看板，markdown 给表格/邮件。"""

    date: str
    metrics: dict[str, Any] = field(default_factory=dict)
    by_shop: list[dict[str, Any]] = field(default_factory=list)
    top_skus: list[dict[str, Any]] = field(default_factory=list)
    trend: list[dict[str, Any]] = field(default_factory=list)
    alerts: list[dict[str, Any]] = field(default_factory=list)
    markdown: str = ""
    saved_to: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "metrics": self.metrics,
            "by_shop": self.by_shop,
            "top_skus": self.top_skus,
            "trend": self.trend,
            "alerts": self.alerts,
            "markdown": self.markdown,
            "saved_to": self.saved_to,
        }


class ReportService:
    """销售日报：聚合 -> 预警 -> 渲染 -> 可选推飞书。"""

    def __init__(self, settings: Settings, feishu: FeishuBitableConnector | None = None) -> None:
        self.sales_dir = settings.sales_data_dir
        # 飞书连接器：配置齐备时走真实连接器，否则给 mock（同步端点仍会 503 拦截，
        # mock 只用于本地链路自测，不会把"没配凭证"伪装成"同步成功"）
        self.feishu = feishu

    # ------------------------------------------------------------------
    # 数据读取
    # ------------------------------------------------------------------
    def available_dates(self) -> list[str]:
        """数据里可出日报的日期（升序）。看板日期选择器用。"""
        rows = self._load_rows()
        return sorted({r["date"] for r in rows})

    def _load_rows(self) -> list[dict[str, Any]]:
        """读取 sales 目录下全部 CSV，做列校验与类型清洗。"""
        if not os.path.isdir(self.sales_dir):
            raise NotFoundError(
                f"销售数据目录不存在：{self.sales_dir}。请按列约定放置 CSV 后重试。"
            )
        rows: list[dict[str, Any]] = []
        seen_files = 0
        for name in sorted(os.listdir(self.sales_dir)):
            if not name.lower().endswith(".csv"):
                continue
            seen_files += 1
            path = os.path.join(self.sales_dir, name)
            with open(path, encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                headers = set(reader.fieldnames or [])
                missing = REQUIRED_COLUMNS - headers
                if missing:
                    raise ValidationError(
                        f"{name} 缺少必需列：{', '.join(sorted(missing))}。"
                        f"必需列：{', '.join(sorted(REQUIRED_COLUMNS))}"
                    )
                for idx, raw in enumerate(reader, 2):
                    row = self._clean_row(raw, name, idx)
                    if row:
                        rows.append(row)
        if not rows:
            raise NotFoundError("销售数据目录里没有可用数据（CSV 为空）。")
        return rows

    @staticmethod
    def _clean_row(raw: dict[str, str], filename: str, line: int) -> dict[str, Any] | None:
        """单行清洗：数值列转数字，脏行跳过并留痕（与批量任务"单行失败隔离"同思路）。"""
        try:
            row: dict[str, Any] = {
                "date": (raw.get("date") or "").strip(),
                "shop": (raw.get("shop") or "").strip() or "未知店铺",
                "sku": (raw.get("sku") or "").strip() or "未知SKU",
                "orders": int(float(raw.get("orders") or 0)),
                "units": int(float(raw.get("units") or 0)),
                "revenue": float(raw.get("revenue") or 0),
            }
        except (TypeError, ValueError):
            logger.warning("report.dirty_row_skipped", extra={"file": filename, "line": line})
            return None
        if not row["date"]:
            logger.warning("report.row_missing_date", extra={"file": filename, "line": line})
            return None
        # 可选列：缺省按 0 处理，对应预警规则自动不触发
        for col in OPTIONAL_COLUMNS:
            try:
                row[col] = float(raw.get(col) or 0)
            except (TypeError, ValueError):
                row[col] = 0.0
        return row

    # ------------------------------------------------------------------
    # 聚合
    # ------------------------------------------------------------------
    def build(self, date_str: str | None = None) -> DailyReport:
        """生成指定日期日报（缺省取数据里最新一天）。"""
        rows = self._load_rows()
        dates = sorted({r["date"] for r in rows})
        target = date_str or dates[-1]
        if target not in dates:
            raise NotFoundError(
                f"数据中没有 {target} 的记录。可用日期范围：{dates[0]} ~ {dates[-1]}"
            )

        day_rows = [r for r in rows if r["date"] == target]
        prev_date = self._prev_date(target, dates)

        metrics = self._aggregate(day_rows)
        by_shop = self._by_shop(day_rows)
        top_skus = self._top_skus(day_rows)
        trend = self._trend(rows, dates, tail=14)
        alerts = self._alerts(day_rows, rows, target, prev_date)

        report = DailyReport(
            date=target,
            metrics=metrics,
            by_shop=by_shop,
            top_skus=top_skus,
            trend=trend,
            alerts=alerts,
        )
        report.markdown = self._render_markdown(report, prev_date)
        report.saved_to = self._persist(report)
        return report

    @staticmethod
    def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "revenue": round(sum(r["revenue"] for r in rows), 2),
            "orders": sum(r["orders"] for r in rows),
            "units": sum(r["units"] for r in rows),
            "ad_spend": round(sum(r.get("ad_spend", 0) for r in rows), 2),
            "shops": len({r["shop"] for r in rows}),
            "skus": len({r["sku"] for r in rows}),
        }

    @staticmethod
    def _by_shop(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        agg: dict[str, dict[str, float]] = defaultdict(lambda: {"revenue": 0.0, "orders": 0, "units": 0, "ad_spend": 0.0})
        for r in rows:
            a = agg[r["shop"]]
            a["revenue"] += r["revenue"]
            a["orders"] += r["orders"]
            a["units"] += r["units"]
            a["ad_spend"] += r.get("ad_spend", 0)
        return [
            {
                "shop": shop,
                "revenue": round(v["revenue"], 2),
                "orders": int(v["orders"]),
                "units": int(v["units"]),
                "ad_spend": round(v["ad_spend"], 2),
            }
            for shop, v in sorted(agg.items(), key=lambda kv: -kv[1]["revenue"])
        ]

    @staticmethod
    def _top_skus(rows: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
        agg: dict[tuple[str, str], dict[str, Any]] = defaultdict(
            lambda: {"revenue": 0.0, "units": 0, "stocks": [], "safe_stock": 0.0}
        )
        for r in rows:
            a = agg[(r["shop"], r["sku"])]
            a["revenue"] += r["revenue"]
            a["units"] += r["units"]
            # 同 SKU 多行时库存取最小值（最紧张的那个快照），未上报库存的行不计入
            if r.get("stock", 0) > 0:
                a["stocks"].append(r["stock"])
            a["safe_stock"] = max(a["safe_stock"], r.get("safe_stock", 0))
        items = []
        for (shop, sku), v in agg.items():
            stock = int(min(v["stocks"])) if v["stocks"] else 0
            items.append(
                {
                    "shop": shop,
                    "sku": sku,
                    "revenue": round(v["revenue"], 2),
                    "units": int(v["units"]),
                    "stock": stock,
                    "safe_stock": int(v["safe_stock"]),
                }
            )
        return sorted(items, key=lambda x: -x["revenue"])[:limit]

    @staticmethod
    def _trend(rows: list[dict[str, Any]], dates: list[str], tail: int = 14) -> list[dict[str, Any]]:
        """近 N 天 GMV/订单趋势，看板折线与环比计算共用。"""
        out = []
        for d in dates[-tail:]:
            day_rows = [r for r in rows if r["date"] == d]
            out.append(
                {
                    "date": d,
                    "revenue": round(sum(r["revenue"] for r in day_rows), 2),
                    "orders": sum(r["orders"] for r in day_rows),
                }
            )
        return out

    @staticmethod
    def _prev_date(target: str, dates: list[str]) -> str | None:
        """目标日期在数据里的前一个营业日（不是自然日回退，避免周末断档误报）。"""
        idx = dates.index(target)
        return dates[idx - 1] if idx > 0 else None

    # ------------------------------------------------------------------
    # 规则预警（确定性规则，不走大模型）
    # ------------------------------------------------------------------
    def _alerts(
        self,
        day_rows: list[dict[str, Any]],
        all_rows: list[dict[str, Any]],
        target: str,
        prev_date: str | None,
    ) -> list[dict[str, Any]]:
        alerts: list[dict[str, Any]] = []

        # 规则 1：SKU 库存低于安全水位
        stock_agg: dict[tuple[str, str], dict[str, float]] = {}
        for r in day_rows:
            key = (r["shop"], r["sku"])
            cur = stock_agg.setdefault(key, {"stock": r.get("stock", 0), "safe": r.get("safe_stock", 0)})
            cur["stock"] = min(cur["stock"], r.get("stock", 0))
            cur["safe"] = max(cur["safe"], r.get("safe_stock", 0))
        for (shop, sku), v in sorted(stock_agg.items()):
            if 0 < v["safe"] > v["stock"]:
                alerts.append(
                    {
                        "level": LEVEL_HIGH,
                        "type": "stock",
                        "message": f"[{shop}] {sku} 库存 {int(v['stock'])} 件，低于安全水位 {int(v['safe'])} 件，建议立即补货",
                    }
                )

        # 规则 2：GMV 环比骤降（按店铺）
        if prev_date:
            prev_rows = [r for r in all_rows if r["date"] == prev_date]
            prev_by_shop: dict[str, float] = defaultdict(float)
            for r in prev_rows:
                prev_by_shop[r["shop"]] += r["revenue"]
            cur_by_shop: dict[str, float] = defaultdict(float)
            for r in day_rows:
                cur_by_shop[r["shop"]] += r["revenue"]
            for shop, cur in cur_by_shop.items():
                prev = prev_by_shop.get(shop, 0)
                if prev > 0 and (prev - cur) / prev > RULES["revenue_drop_ratio"]:
                    pct = round((prev - cur) / prev * 100, 1)
                    alerts.append(
                        {
                            "level": LEVEL_MID,
                            "type": "revenue_drop",
                            "message": f"[{shop}] GMV 环比下降 {pct}%（{round(prev, 2)} -> {round(cur, 2)}），请核查流量与广告",
                        }
                    )

        # 规则 3：SKU 差评率超标
        rv_agg: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: {"reviews": 0.0, "bad": 0.0})
        for r in day_rows:
            key = (r["shop"], r["sku"])
            rv_agg[key]["reviews"] += r.get("reviews", 0)
            rv_agg[key]["bad"] += r.get("bad_reviews", 0)
        for (shop, sku), v in sorted(rv_agg.items()):
            if v["reviews"] >= 5 and v["bad"] / v["reviews"] > RULES["bad_review_ratio"]:
                pct = round(v["bad"] / v["reviews"] * 100, 1)
                alerts.append(
                    {
                        "level": LEVEL_MID,
                        "type": "bad_reviews",
                        "message": f"[{shop}] {sku} 当日差评率 {pct}%（{int(v['bad'])}/{int(v['reviews'])}），超过 {RULES['bad_review_ratio'] * 100:.0f}% 阈值",
                    }
                )

        # 规则 4：店铺广告花费占比超标
        ad_by_shop: dict[str, dict[str, float]] = defaultdict(lambda: {"ad": 0.0, "rev": 0.0})
        for r in day_rows:
            a = ad_by_shop[r["shop"]]
            a["ad"] += r.get("ad_spend", 0)
            a["rev"] += r["revenue"]
        for shop, v in sorted(ad_by_shop.items()):
            if v["rev"] > 0 and v["ad"] / v["rev"] > RULES["ad_spend_ratio"]:
                pct = round(v["ad"] / v["rev"] * 100, 1)
                alerts.append(
                    {
                        "level": LEVEL_MID,
                        "type": "ad_spend",
                        "message": f"[{shop}] 广告花费占 GMV {pct}%，超过 {RULES['ad_spend_ratio'] * 100:.0f}% 阈值，检查投放效率",
                    }
                )

        # 稳定排序：高级别在前，同级按类型
        return sorted(alerts, key=lambda a: (0 if a["level"] == LEVEL_HIGH else 1, a["type"]))

    # ------------------------------------------------------------------
    # 渲染与持久化
    # ------------------------------------------------------------------
    @staticmethod
    def _render_markdown(report: DailyReport, prev_date: str | None) -> str:
        m = report.metrics
        lines = [
            f"# 跨境电商销售日报 · {report.date}",
            "",
        ]
        if prev_date:
            lines.append(f"> 对比上一营业日：{prev_date}")
            lines.append("")
        lines += [
            "## 核心指标",
            f"- GMV：¥{m['revenue']:,.2f}",
            f"- 订单数：{m['orders']}｜销量：{m['units']} 件",
            f"- 广告花费：¥{m['ad_spend']:,.2f}"
            + (f"（占 GMV {round(m['ad_spend'] / m['revenue'] * 100, 1)}%）" if m["revenue"] else ""),
            f"- 覆盖：{m['shops']} 个店铺 / {m['skus']} 个 SKU",
            "",
        ]
        if report.alerts:
            lines.append("## 预警")
            for a in report.alerts:
                lines.append(f"- **[{a['level']}**] {a['message']}")
            lines.append("")
        if report.by_shop:
            lines.append("## 分店铺")
            lines.append("| 店铺 | GMV | 订单 | 销量 | 广告 |")
            lines.append("|---|---|---|---|---|")
            for s in report.by_shop:
                lines.append(f"| {s['shop']} | {s['revenue']:,.2f} | {s['orders']} | {s['units']} | {s['ad_spend']:,.2f} |")
            lines.append("")
        if report.top_skus:
            lines.append("## Top SKU")
            lines.append("| 店铺 | SKU | GMV | 销量 | 库存 |")
            lines.append("|---|---|---|---|---|")
            for s in report.top_skus:
                stock_txt = f"{s['stock']}（安全 {s['safe_stock']}）" if s["safe_stock"] else "—"
                lines.append(f"| {s['shop']} | {s['sku']} | {s['revenue']:,.2f} | {s['units']} | {stock_txt} |")
        return "\n".join(lines)

    @staticmethod
    def _persist(report: DailyReport) -> str:
        """日报落盘，供审计与后续推送（邮件 / 飞书机器人）复用。"""
        try:
            os.makedirs(REPORT_DIR, exist_ok=True)
            path = os.path.join(REPORT_DIR, f"daily_{report.date}.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write(report.markdown)
            return path
        except OSError as exc:
            # 落盘失败不阻断日报返回：看板照样能渲染，只在日志留痕
            logger.warning("report.persist_failed", extra={"error": str(exc)})
            return ""

    # ------------------------------------------------------------------
    # 飞书同步
    # ------------------------------------------------------------------
    def sync_to_feishu(self, report: DailyReport) -> dict[str, Any]:
        """把日报写进飞书多维表格。凭证缺失时 503 快速失败，绝不静默 mock。"""
        if self.feishu is None:
            # 缺配置按项目约定走 503（服务端问题，重试无用），与 LLM 缺 Key 同语义
            raise ConfigError(
                "尚未配置飞书集成：请在 backend/.env 填写 FEISHU_APP_ID / FEISHU_APP_SECRET / "
                "FEISHU_BITABLE_APP_TOKEN / FEISHU_TABLE_ID 后重启服务"
            )
        try:
            result = self.feishu.push_daily_report(report.to_dict())
        except (ConnectionError, ValueError) as exc:
            raise ExternalApiError(f"飞书多维表格写入失败：{exc}") from exc
        # 注意 extra 键名不能用 created / message 等 LogRecord 保留字，会直接 KeyError
        logger.info("report.feishu_synced", extra={"date": report.date, "rows_created": result.get("created")})
        return {"date": report.date, **result}
