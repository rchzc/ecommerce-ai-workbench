"""电商 AI 数据中台 —— 数据接入 → 清洗校验 → 指标聚合 → 出口回写。

本包负责「数据从哪里来、变成什么形状、怎么流出去」，不含任何 Agent 与模型编排：

    连接器（Shopify / Amazon SP-API / 飞书多维表格）
        ↓ 拉取 + 归一成 CSV / Markdown
    表格管道 tabular + 规则校验 validators
        ↓ 把任意嵌套 JSON 摊平成二维表，并给出 pass / warn / fail 结论
    指标聚合 report（日报 + 4 条规则预警）
        ↓ 不烧模型钱也能出结论
    批量任务 batch（有状态、可断点、幂等）
        ↓ 一次几百行，逐行独立成败
    流水线 pipeline（定时编排 + 幂等状态 + 失败不中断）
        ↓ 产出落 data/reports/*.md 并（可选）写回飞书

对外只暴露下面这些名字。业务侧不用知道内部文件怎么切分 ——
重构目录结构时只要 __all__ 不变，调用方零改动。
"""
from __future__ import annotations

from .batch import BatchJob, BatchService, RowResult
from .config import (
    DATA_DIR,
    DEFAULT_JOB_DIR,
    DOCS_DIR,
    IMPORT_DIR,
    PACKAGE_ROOT,
    REPORT_DIR,
    SALES_DIR,
    STATE_PATH,
    Settings,
    load_settings,
)
from .pipeline import PipelineService, parse_schedule
from .report import DailyReport, ReportService
from .tabular import flatten, parse_csv, to_csv
from .validators import Issue, ValidationResult, validate_output

__all__ = [
    # 配置与路径
    "Settings",
    "load_settings",
    "PACKAGE_ROOT",
    "DATA_DIR",
    "DOCS_DIR",
    "IMPORT_DIR",
    "DEFAULT_JOB_DIR",
    "REPORT_DIR",
    "SALES_DIR",
    "STATE_PATH",
    # 表格管道
    "parse_csv",
    "flatten",
    "to_csv",
    # 规则校验
    "Issue",
    "ValidationResult",
    "validate_output",
    # 指标聚合
    "DailyReport",
    "ReportService",
    # 批量任务
    "RowResult",
    "BatchJob",
    "BatchService",
    # 流水线
    "PipelineService",
    "parse_schedule",
]
