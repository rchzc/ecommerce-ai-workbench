"""全链路流水线：店铺拉数 → 日报聚合（规则预警）→（可选）LLM 解读 → 飞书同步。

这是「数据决策闭环」的自动化层。四段链路每段都可独立复用，
Pipeline 只做编排与状态管理，不含业务规则：

    1. 数据刷新   连接器（真实 SP-API / 演示模拟导出）把当日销售行写入 data/sales/
                  —— 幂等：当日已有数据则跳过，重复执行不会产生脏数据
    2. 日报聚合   ReportService.build()：指标汇总 + 4 条规则预警（不烧模型钱）
    3. LLM 解读   可选（PIPELINE_USE_LLM，默认关）：轻量模型给 80 字经营解读，
                  规则覆盖不了的模糊判断才交给模型
    4. 飞书同步   ReportService.sync_to_feishu()：写多维表格

三个关键工程决策：
1. **幂等优先**。调度器每天触发一次 + 人手动点 N 次，都不能产生重复日报：
   数据刷新按日期判重；飞书同步按状态文件（data/pipeline_state.json）判重，
   当日已成功就跳过，force=true 才重跑。
2. **失败不中断服务**。任何一步失败都记录进状态文件并返回结构化结果，
   绝不让调度循环崩掉 —— 今天的失败不应该拖垮明天 9 点的定时任务。
3. **调度器极简**。asyncio 单协程循环算出距下次触发的秒数后睡眠，
   不引入 APScheduler 之类的重依赖 —— 单机单任务场景，复杂度不值得。
"""
from __future__ import annotations

import asyncio
import csv
import json
import logging
import os
import random
import time
from datetime import date as date_cls
from datetime import datetime, timedelta
from typing import Any

from ..config import Settings
from ..core.llm import LLMGateway, parse_json_lenient
from ..errors import ConfigError, ExternalApiError
from .report_service import ReportService

logger = logging.getLogger(__name__)

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATE_PATH = os.path.join(BACKEND_DIR, "data", "pipeline_state.json")
# 流水线自己产出的销售行单独落文件，不污染 sample_sales.csv（原始导入与自动产出分离）
PIPELINE_SALES_FILE = "pipeline_rows.csv"

# 与 report_service 的列约定一致
SALES_COLUMNS = (
    "date", "shop", "sku", "orders", "units", "revenue",
    "ad_spend", "stock", "safe_stock", "reviews", "bad_reviews",
)


def parse_schedule(schedule: str) -> tuple[int, int]:
    """解析 HH:MM，返回 (时, 分)。范围非法同样抛 ValueError（启动时已校验，双保险）。"""
    parts = schedule.split(":")
    if len(parts) != 2:
        raise ValueError(schedule)
    hh, mm = int(parts[0]), int(parts[1])
    if not (0 <= hh < 24 and 0 <= mm < 60):
        raise ValueError(schedule)
    return hh, mm


class PipelineService:
    """编排四段链路 + 管理运行状态。"""

    def __init__(
        self,
        settings: Settings,
        report_service: ReportService,
        gateway: LLMGateway | None = None,
    ) -> None:
        self.settings = settings
        self.report = report_service
        self.gateway = gateway
        self.last_run: dict[str, Any] = self._load_state()

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------
    async def run(self, force: bool = False) -> dict[str, Any]:
        """执行整条链路。返回结构化结果，任何一步失败都不抛异常（记录状态后返回）。"""
        today = date_cls.today().isoformat()

        # 幂等闸门：今天已经成功同步过就直接跳过
        if (
            not force
            and self.last_run.get("date") == today
            and self.last_run.get("status") == "ok"
        ):
            return {
                "skipped": True,
                "date": today,
                "reason": "今日日报已同步，幂等跳过（force=true 可强制重跑）",
                "last_run": self.last_run,
            }

        started = time.perf_counter()
        steps: dict[str, Any] = {}

        # 1. 数据刷新（连接器拉数，幂等）
        try:
            steps["refresh"] = self._refresh_data(today)
        except Exception as exc:  # noqa: BLE001 - 单步失败不拖垮整条链路
            logger.exception("pipeline.refresh_failed")
            steps["refresh"] = {"status": "failed", "error": str(exc)[:200]}

        # 2. 日报聚合 + 规则预警（数据刷新失败时退回最近可用日期的日报）
        try:
            report = self.report.build()
            steps["report"] = {
                "status": "ok",
                "date": report.date,
                "alerts": len(report.alerts),
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception("pipeline.report_failed")
            return self._finish(today, "failed", steps, error=f"日报生成失败：{exc}")

        # 3. 可选 LLM 解读（默认关）
        if self.settings.pipeline_use_llm and self.gateway is not None:
            steps["ai_summary"] = await self._llm_summary(report)

        # 4. 飞书同步
        try:
            sync = self.report.sync_to_feishu(report)
            steps["feishu"] = {"status": "ok", "rows_created": sync.get("created", 0)}
        except (ConfigError, ExternalApiError) as exc:
            logger.warning("pipeline.feishu_failed", extra={"error": str(exc)[:200]})
            steps["feishu"] = {"status": "failed", "error": str(exc)[:200]}
            return self._finish(today, "failed", steps, error=str(exc)[:200])

        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        return self._finish(
            today,
            "ok",
            steps,
            report_date=report.date,
            elapsed_ms=elapsed_ms,
        )

    def _finish(
        self,
        date: str,
        status: str,
        steps: dict[str, Any],
        error: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        """落状态文件并返回结果。无论成败都留痕，排查和幂等判重都靠它。"""
        result = {"date": date, "status": status, "steps": steps, **extra}
        if error:
            result["error"] = error
        self.last_run = result
        self._save_state()
        logger.info(
            "pipeline.finished",
            extra={"status": status, "date": date, "steps": json.dumps(steps, ensure_ascii=False)[:300]},
        )
        return result

    # ------------------------------------------------------------------
    # 第 1 步：数据刷新
    # ------------------------------------------------------------------
    def _refresh_data(self, today: str) -> dict[str, Any]:
        if not self.settings.pipeline_refresh_data:
            return {"status": "skipped", "reason": "PIPELINE_REFRESH_DATA=false"}

        dates = self.report.available_dates()
        if today in dates:
            return {"status": "skipped", "reason": "今日数据已存在（幂等跳过）", "date": today}

        rows = self._fetch_store_rows(today, dates)
        self._append_rows(rows)
        return {
            "status": "ok",
            "date": today,
            "appended": len(rows),
            "mode": rows[0]["_mode"] if rows else "unknown",
        }

    def _fetch_store_rows(self, today: str, dates: list[str]) -> list[dict[str, Any]]:
        """从店铺连接器取当日销售行。

        真实模式：配置了 Amazon SP-API 凭证时拉真实订单并按店铺聚合；
        演示模式：按日期做种子的模拟店铺导出（与连接器 mock 同思路）——
        同一天重复执行结果完全一致，保证幂等。
        """
        api_key = os.getenv("AMAZON_CLIENT_ID")
        if api_key:
            return self._rows_from_amazon(today)

        # 演示模式：以昨日行为基线做带种子的波动，保证趋势连续、可解释
        rows = self.report._load_rows()  # noqa: SLF001 - 同层服务复用私有读取，避免重复实现
        last_rows = [r for r in rows if r["date"] == dates[-1]] if dates else []
        rng = random.Random(today)  # noqa: S311 - 演示数据，非安全场景
        out = []
        for r in last_rows:
            orders = max(1, int(r["orders"] * rng.uniform(0.9, 1.15)))
            units = max(orders, int(orders * rng.uniform(1.25, 1.4)))
            revenue = round(units * rng.uniform(55.0, 62.0), 2)
            out.append(
                {
                    "date": today,
                    "shop": r["shop"],
                    "sku": r["sku"],
                    "orders": orders,
                    "units": units,
                    "revenue": revenue,
                    "ad_spend": round(revenue * rng.uniform(0.09, 0.12), 2),
                    # 库存随销量递减，让补货预警规则在流水线里持续有意义
                    "stock": max(0, int(r.get("stock", 0)) - units),
                    "safe_stock": r.get("safe_stock", 0),
                    "reviews": rng.randint(8, 15),
                    "bad_reviews": rng.randint(0, 2),
                    "_mode": "mock",
                }
            )
        if not out:
            # 数据目录还是空的：给两条起步行，保证链路能跑通
            out = [
                {"date": today, "shop": "Amazon US", "sku": "B0DEMO0001", "orders": 70, "units": 95,
                 "revenue": 5400.0, "ad_spend": 500.0, "stock": 180, "safe_stock": 150,
                 "reviews": 20, "bad_reviews": 2, "_mode": "mock"},
                {"date": today, "shop": "Shopify", "sku": "B0DEMO0001", "orders": 30, "units": 40,
                 "revenue": 2300.0, "ad_spend": 180.0, "stock": 300, "safe_stock": 200,
                 "reviews": 10, "bad_reviews": 1, "_mode": "mock"},
            ]
        return out

    def _rows_from_amazon(self, today: str) -> list[dict[str, Any]]:
        """真实模式：SP-API 订单按店铺聚合（units 按订单数近似，SP-API 单接口拿不到件数）。"""
        from ..connectors.amazon_sp_api import AmazonSpApiConnector

        conn = AmazonSpApiConnector(
            seller_id=os.getenv("AMAZON_SELLER_ID", ""),
            marketplace_id=os.getenv("AMAZON_MARKETPLACE_ID", "ATVPDKIKX0DER"),
            client_id=os.getenv("AMAZON_CLIENT_ID", ""),
            client_secret=os.getenv("AMAZON_CLIENT_SECRET", ""),
            refresh_token=os.getenv("AMAZON_REFRESH_TOKEN", ""),
            region=os.getenv("AMAZON_REGION", "na"),
        )
        orders = conn.fetch_orders(days=1)
        agg: dict[str, dict[str, float]] = {}
        for o in orders:
            shop = o.get("SalesChannel", "Amazon")
            amount = float((o.get("OrderTotal") or {}).get("Amount") or 0)
            a = agg.setdefault(shop, {"orders": 0, "revenue": 0.0})
            a["orders"] += 1
            a["revenue"] += amount
        return [
            {
                "date": today,
                "shop": shop,
                "sku": "AGGREGATED",
                "orders": int(v["orders"]),
                "units": int(v["orders"]),
                "revenue": round(v["revenue"], 2),
                "ad_spend": 0.0,
                "stock": 0.0,
                "safe_stock": 0.0,
                "reviews": 0.0,
                "bad_reviews": 0.0,
                "_mode": "real",
            }
            for shop, v in agg.items()
        ] or []

    def _append_rows(self, rows: list[dict[str, Any]]) -> None:
        """追加到配置的销售数据目录（与日报数据源同一目录，保证下一步能读到）。"""
        path = os.path.join(self.settings.sales_data_dir, PIPELINE_SALES_FILE)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        is_new = not os.path.isfile(path)
        with open(path, "a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(SALES_COLUMNS), extrasaction="ignore")
            if is_new:
                writer.writeheader()
            for row in rows:
                writer.writerow(row)
        logger.info("pipeline.rows_appended", extra={"file": path, "rows": len(rows)})

    # ------------------------------------------------------------------
    # 第 3 步：可选 LLM 解读
    # ------------------------------------------------------------------
    async def _llm_summary(self, report: Any) -> dict[str, Any]:
        """轻量模型给一段经营解读。失败只降级，不影响链路其余部分。"""
        system = (
            "你是跨境电商运营分析师。基于给出的指标与预警，输出不超过 80 字的中文经营解读，"
            "先说整体态势，再点出最需要处理的一件事。只输出 JSON：{\"summary\": \"...\"}"
        )
        user = json.dumps(
            {
                "date": report.date,
                "metrics": report.metrics,
                "alerts": [a["message"] for a in report.alerts],
            },
            ensure_ascii=False,
        )
        try:
            raw, meta = await self.gateway.complete(system, user, temperature=0.3)
            parsed = parse_json_lenient(raw)
            summary = (parsed or {}).get("summary") or raw[:120]
            return {"status": "ok", "model": meta.get("model", ""), "summary": summary}
        except Exception as exc:  # noqa: BLE001 - 解读是锦上添花，失败不拦链路
            logger.warning("pipeline.llm_summary_failed", extra={"error": str(exc)[:200]})
            return {"status": "failed", "error": str(exc)[:200]}

    # ------------------------------------------------------------------
    # 状态持久化
    # ------------------------------------------------------------------
    def _load_state(self) -> dict[str, Any]:
        """启动时恢复上次运行状态（幂等判重的依据）。"""
        try:
            with open(STATE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_state(self) -> None:
        """写失败不致命：大不了重复同步一天，不该因状态文件写不进去崩掉链路。"""
        try:
            os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
            with open(STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(self.last_run, f, ensure_ascii=False, indent=2)
        except OSError:
            logger.warning("pipeline.state_save_failed", extra={"path": STATE_PATH})


# ----------------------------------------------------------------------
# 定时调度：单协程循环，睡到下一个触发点再跑，循环往复
# ----------------------------------------------------------------------
async def scheduler_loop(service: PipelineService, schedule: str) -> None:
    hh, mm = parse_schedule(schedule)
    logger.info("pipeline.scheduler_started", extra={"schedule": schedule})
    while True:
        now = datetime.now()
        target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        logger.info(
            "pipeline.next_run",
            extra={"at": target.isoformat(timespec="minutes"), "in_seconds": int((target - now).total_seconds())},
        )
        try:
            await asyncio.sleep((target - now).total_seconds())
        except asyncio.CancelledError:
            logger.info("pipeline.scheduler_stopped")
            raise
        try:
            await service.run()
        except Exception:  # noqa: BLE001 - 定时任务绝不能把循环跑死
            logger.exception("pipeline.scheduled_run_failed")
