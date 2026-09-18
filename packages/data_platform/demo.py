"""数据中台一键演示：不需要任何 API Key，不联网。

    python demo.py

演示顺序就是数据的真实流向，**每一步都打印可核对的实际数字**，
没有"看起来在跑"的演示：

    1. 配置与路径   —— 路径常量只有一个来源，Settings 继承共享层
    2. 数据接入     —— 连接器把平台数据归一成知识库文档（接真实店铺 = 加一个连接器）
    3. 表格管道     —— 任意嵌套 JSON 摊平成二维表 + 规则校验出 pass/warn/fail
    4. 日报聚合     —— 指标汇总 + 4 条规则预警（不烧模型钱也能出结论）
    5. 批量任务     —— 一次几百行，逐行独立成败 + 幂等键去重
    6. 每日流水线   —— 刷新 → 日报 → 同步，带幂等判重与失败留痕
    7. 已知边界     —— 明确说清楚哪些是没做的

跑完会在 data/reports/ 下留一份真实日报（已被 .gitignore 排除）。
"""
from __future__ import annotations

import asyncio
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from _bootstrap import ensure_paths  # noqa: E402

ensure_paths(_HERE)

# 离线跑：不需要 key，也不会因为没配 Key 而在配置校验阶段就崩掉。
os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("VECTOR_BACKEND", "lexical")
os.environ.setdefault("LOG_LEVEL", "WARNING")

from data_platform import config as cfg  # noqa: E402
from data_platform.batch import STATUS_DONE, BatchService  # noqa: E402
from data_platform.connectors.feishu_bitable import MockFeishuBitableConnector  # noqa: E402
from data_platform.connectors.shopify import (  # noqa: E402
    ShopifyConnector,
    mock_orders,
    mock_reviews,
)
from data_platform.pipeline import PipelineService, parse_schedule  # noqa: E402
from data_platform.report import ReportService  # noqa: E402
from data_platform.tabular import flatten  # noqa: E402
from data_platform.validators import validate_output  # noqa: E402

LINE = "=" * 74

LISTING_SCHEMA = {
    "title": "string，产品标题",
    "bullets": ["string，五点描述"],
    "keywords": {"core": ["string"], "long_tail": ["string"]},
}


def step(n: int, title: str) -> None:
    print(f"\n{LINE}\n  {n}. {title}\n{LINE}")


def _clean_demo_artifacts(settings) -> list[str]:
    """清掉上一次演示留下的运行期产物，保证演示可重复。

    不清的话第二次跑会出现"刷新被跳过"（当日数据已在）、"日报日期变成今天"
    这类现象 —— 逻辑是对的，但演示看起来像坏了。这些都是运行期产物
    （已被 .gitignore 排除），删掉不影响任何已提交的语料或样例数据。
    """
    import shutil

    removed: list[str] = []
    dirs = [
        os.path.join(settings.import_dir, "docs_demo"),
        os.path.join(settings.import_dir, "jobs_demo"),
        settings.reports_dir,
        settings.jobs_dir,
    ]
    for path in dirs:
        if os.path.isdir(path):
            shutil.rmtree(path)
            removed.append(os.path.relpath(path, settings.data_dir))
    for path in (
        cfg.STATE_PATH,
        os.path.join(settings.sales_data_dir, "pipeline_rows.csv"),
    ):
        if os.path.isfile(path):
            os.remove(path)
            removed.append(os.path.relpath(path, settings.data_dir))
    return removed


class _OfflineAgentService:
    """替身：模拟"上层业务 Agent 服务"，完全不碰模型。

    它的存在本身就是演示的一部分 —— 批量任务只依赖一个协议
    （`output_schema()` + `async run()`），不认识 LLM 网关。
    所以这一层可以脱离模型单独测试、单独演进。
    """

    def __init__(self) -> None:
        self.calls = 0

    def output_schema(self, agent_name: str) -> dict:
        from ecom_shared.errors import NotFoundError

        if agent_name != "listing":
            raise NotFoundError(f"未知 Agent: {agent_name}")
        return LISTING_SCHEMA

    async def run(self, agent_name: str, payload: dict) -> dict:
        from ecom_shared.errors import ModelCallError

        self.calls += 1
        # 指定商品在模拟里调用失败：演示"单行失败不拖垮整批"
        if payload.get("product") == "水杯":
            raise ModelCallError("模拟模型调用超时")
        return {
            "agent": agent_name,
            "domain": "listing",
            "data": {
                "title": f"{payload.get('product', '商品')} · 高转化标题",
                "bullets": ["卖点一", "卖点二", "卖点三"],
                "keywords": {"core": ["foldable"], "long_tail": ["foldable umbrella"]},
            },
            "knowledge": [{"source": "01_高转化标题撰写方法.md", "text": "", "domain": "listing"}],
            "meta": {"model": "offline-stub"},
        }


async def main() -> None:
    print(f"{LINE}\n  电商 AI 生态 · 数据中台（ecom-data-platform）演示\n{LINE}")

    # ---------------------------------------------------------------- 1
    step(1, "配置与路径：路径常量只有一个来源")
    settings = cfg.load_settings()
    removed = _clean_demo_artifacts(settings)
    if removed:
        print(f"  已清理上次演示的产物：{removed}（均为运行期产物，已 .gitignore）")
    print(f"  包根目录    : {cfg.PACKAGE_ROOT}")
    print(f"  数据目录    : {settings.data_dir}")
    for label, path in (
        ("语料 docs", settings.docs_dir),
        ("导入 import", settings.import_dir),
        ("批量 jobs", settings.jobs_dir),
        ("报表 reports", settings.reports_dir),
    ):
        exists = "存在" if os.path.isdir(path) else "待创建"
        print(f"    {label:14s}{path}  [{exists}]")
    print(f"  模型接入    : {settings.provider_label}（{settings.provider}）"
          f"{'  ← 离线' if settings.is_mock else ''}")
    print("  说明：以前 7 个模块各自按「文件在目录树里的深度」算一次基准目录，")
    print("        包一挪位置就静默读错目录；现在只有 config.py 一个来源，")
    print("        改 DATA_DIR 一个环境变量，上面所有目录一起走。")

    # ---------------------------------------------------------------- 2
    step(2, "数据接入：连接器把平台数据归一成知识库文档")
    connector = ShopifyConnector(shop="demo-shop", token="offline")
    # 落在一个演示子目录里，不覆盖仓库里已提交的语料
    demo_docs = os.path.join(settings.import_dir, "docs_demo")
    result = connector.write_docs(mock_orders(), mock_reviews(), docs_dir=demo_docs)
    print(f"  连接器      : ShopifyConnector（离线用 mock_orders / mock_reviews 喂数据）")
    print(f"  归一结果    : 订单 {result['orders']} 条 · 评论 {result['reviews']} 条")
    for rel in result["written"]:
        print(f"    写出 {rel}")
    print(f"\n  归一后的片段：")
    for line in (
        open(os.path.join(demo_docs, "review", "live_reviews.md"), encoding="utf-8")
        .read()
        .splitlines()[:6]
    ):
        print(f"    {line}")
    print("\n  说明：连接器只做「拉取 → 清洗归一 → 写进 data/docs/<域>」，")
    print("        重建索引由共享集群的检索层负责 —— 所以「接一个新平台」")
    print("        等于新增一个连接器文件，上层业务零改动。")

    # ---------------------------------------------------------------- 3
    step(3, "表格管道与规则校验：把任意嵌套结构变成可校验的二维表")
    nested = {
        "title": "折叠伞",
        "bullets": ["防晒", "轻便"],
        "keywords": {"core": ["umbrella"], "long_tail": ["fold umbrella"]},
        "meta": {"model": "qwen-plus", "tokens": 812},
    }
    flat = flatten(nested)
    print(f"  摊平结果    : {len(flat)} 列")
    for key, value in flat.items():
        print(f"    {key:18s} = {value[:44]}")
    print("    列表整体存成 JSON，不做 0/1 逐项展开 —— 元素是对象时展开会散架。")

    for label, payload in (
        ("完整输出", nested),
        ("缺一个字段", {"title": "折叠伞", "bullets": ["防晒"], "keywords": {}}),
        ("占位内容", {"title": "N/A", "bullets": ["待补充"],
                      "keywords": {"core": ["a"], "long_tail": ["b"]}}),
        ("结构崩塌", {"title": "只剩一个字段"}),
    ):
        verdict = validate_output(payload, LISTING_SCHEMA)
        rules = sorted({i.rule for i in verdict.issues}) or ["-"]
        print(f"\n  {label:10s} → {verdict.status.upper():4s}  规则={rules}")
    print("\n  说明：pass/warn/fail 三档不是装饰 —— warn 允许入库但要标记需复核，")
    print("        fail 直接拦下不写库，避免脏数据流到下游看板。")

    # ---------------------------------------------------------------- 4
    step(4, "日报聚合：指标汇总 + 满 4 条规则预警（零模型成本）")
    report_service = ReportService(settings, feishu=MockFeishuBitableConnector())
    print(f"  可选日期    : {report_service.available_dates()}")
    report = report_service.build()
    print(f"  出报日期    : {report.date}")
    print(f"  核心指标    : GMV={report.metrics['revenue']:,.2f}  "
          f"订单={report.metrics['orders']}  销量={report.metrics['units']}  "
          f"店铺={report.metrics['shops']}")
    print(f"  分店铺      : {[(s['shop'], s['revenue']) for s in report.by_shop]}")
    print(f"  预警条数    : {len(report.alerts)}")
    for alert in report.alerts[:4]:
        print(f"    [{alert['level']:4s}] {alert['message']}")
    print(f"  落盘        : {report.saved_to or '（未落盘）'}")
    print("\n  说明：这四条规则都能解释「为什么报警」，且不花一分钱 token。")
    print("        只有规则覆盖不了的模糊判断才交给模型 —— 这是刻意取舍。")

    # ---------------------------------------------------------------- 5
    step(5, "批量任务：一次多行、逐行独立成败、幂等键去重")
    fake = _OfflineAgentService()
    batch = BatchService(
        agent_service=fake,
        max_rows=settings.batch_max_rows,
        job_dir=os.path.join(settings.import_dir, "jobs_demo"),
    )
    job = batch.create_job(
        "listing",
        [{"product": "折叠伞"}, {"product": "水杯"}, {"product": "登山杖"}],
        idempotency_key="demo-batch-1",
    )
    while job.status in ("pending", "running"):
        await asyncio.sleep(0.01)
        job = batch.get_job(job.job_id)
    print(f"  任务 {job.job_id[:8]}  status={job.status}  （期望 done={STATUS_DONE}）")
    print(f"  总计 {job.total} 行 · 成功 {job.ok_count} · 失败 {job.failed_count}")
    for row in job.rows:
        mark = "OK  " if row.status == "ok" else "FAIL"
        detail = row.error or (row.validation or {}).get("status", "")
        print(f"    [{mark}] 第 {row.index} 行  {detail}")

    again = batch.create_job("listing", [{"product": "折叠伞"}], idempotency_key="demo-batch-1")
    print(f"\n  幂等键重复提交 → 复用同一任务={again.job_id == job.job_id}  "
          f"模型实际调用次数={fake.calls}")
    print("  说明：n8n 重试、前端连点、网络重传都不该触发第二次模型调用。")

    # ---------------------------------------------------------------- 6
    step(6, "每日流水线：刷新 → 日报 →（可选）模型解读 → 同步")
    print(f"  调度表达式  : '09:00' → 解析为 {parse_schedule('09:00')}（HH, MM）")
    pipeline = PipelineService(settings, report_service=report_service)
    first = await pipeline.run(force=True)
    print(f"  首次执行    : status={first['status']}")
    for name, detail in first["steps"].items():
        extra = {
            "refresh": f"  mode={detail.get('mode')}  追加 {detail.get('appended')} 行",
            "report": f"  date={detail.get('date')}  预警 {detail.get('alerts')} 条",
            "ai_summary": "  ← 默认关闭：规则预警已可解释，模型解读是加分项",
            "feishu": f"  rows_created={detail.get('rows_created')}",
        }.get(name, "")
        reason = detail.get("reason") or detail.get("error") or ""
        print(f"    {name:11s} {detail.get('status')}" + (f"  （{reason}）" if reason else "") + extra)

    second = await pipeline.run()
    print(f"\n  当日再执行  : {second.get('reason')}")
    print(f"  幂等跳过    : {second.get('skipped')}  "
          f"（调度器每天触发一次 + 人手点 N 次，都只产生一份日报）")
    print(f"  状态文件    : {cfg.STATE_PATH}")
    print("  说明：任何一步失败都记进状态文件并返回结构化结果，")
    print("        绝不让调度循环崩掉 —— 今天的失败不该拖垮明天 9 点的任务。")

    # ---------------------------------------------------------------- 7
    step(7, "已知边界：明确说清楚哪些是没做的")
    print("  · 批量任务状态存进程内存 + 落盘 JSON，多实例部署需换任务队列")
    print("  · 飞书同步按天判重，不做行级增量（改一行要重跑当天）")
    print("  · 预警阈值写在 report.py 的 RULES 常量里，改阈值要发版（未做配置化）")
    print("  · 连接器的真实分支（SP-API / Admin API）需要商家授权，")
    print("    仓库只带 mock 路径，真实分支的代码在但没跑过真店铺")

    print(f"\n{LINE}\n  演示结束。接真实数据：在 .env 填平台凭证，代码零改动。\n{LINE}")


if __name__ == "__main__":
    asyncio.run(main())
