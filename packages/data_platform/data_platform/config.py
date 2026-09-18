"""数据中台的配置与路径常量。

**路径常量集中在这里，是本包从老布局搬出来时顺手修掉的一个真实隐患。**

老代码里每个模块都自己算一遍基准目录 —— 连续调用三次「取上级目录」，
从 `__file__` 一路退回仓库根。这种写法有两个问题：

1. **深度一变就错，而且不报错。** 文件往上挪一层，基准目录就指到别处，
   程序照常启动，只是读不到数据目录、报表写到奇怪的地方。这次包重构正好踩到这个坑。
2. **同一个事实被定义了 7 次。** 想把数据目录挪到别处，要改 7 个文件，
   漏一个就是"有的模块读新目录、有的读旧目录"。

现在只有一个来源：本文件。所有模块 `from ..config import DOCS_DIR` 即可。

Settings 继承自 `ecom_shared.Settings`（共享集群的配置），只**追加**本包自己的字段。
这样 `LLMGateway(settings)` 能直接接收本包的 Settings —— 共享层不需要知道业务字段，
业务层却能拿到全套共享配置，这是分层的价值。
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from ecom_shared.config import REPO_ROOT, Settings as SharedSettings
from ecom_shared.errors import ConfigError

# 本文件位于 packages/data_platform/data_platform/config.py
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PACKAGE_ROOT = os.path.dirname(_THIS_DIR)  # packages/data_platform

# ---------------------------------------------------------------------------
# 路径：支持环境变量覆盖，默认落在包自己的 data/ 下
# 环境变量覆盖是为了让容器部署时能把数据挂到卷上，而不用改代码
# ---------------------------------------------------------------------------
DATA_DIR = os.getenv("DATA_DIR") or os.path.join(PACKAGE_ROOT, "data")
DOCS_DIR = os.path.join(DATA_DIR, "docs")          # 知识库语料 <domain>/*.md
IMPORT_DIR = os.path.join(DATA_DIR, "import")      # 平台导出文件落盘处
DEFAULT_JOB_DIR = os.path.join(DATA_DIR, "jobs")   # 批量任务产物
REPORT_DIR = os.path.join(DATA_DIR, "reports")     # 日报与看板快照
SALES_DIR = os.getenv("SALES_DIR") or os.path.join(DATA_DIR, "sales")  # 销售明细 CSV
STATE_PATH = os.path.join(DATA_DIR, "pipeline_state.json")             # 流水线幂等状态


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _env_int(key: str, default: int) -> int:
    raw = _env(key, str(default))
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"配置项 {key}={raw!r} 不是合法整数") from exc


@dataclass(frozen=True)
class Settings(SharedSettings):
    """共享配置 + 数据中台自己的配置。

    所有新增字段都有默认值：测试可以直接 `Settings(**{})` 式构造，
    不必为了跑一个单测去凑飞书凭证。
    """

    # --- 路径 ---
    data_dir: str = DATA_DIR
    docs_dir: str = DOCS_DIR
    import_dir: str = IMPORT_DIR
    jobs_dir: str = DEFAULT_JOB_DIR
    reports_dir: str = REPORT_DIR
    sales_data_dir: str = SALES_DIR
    # --- 飞书多维表格（可选集成）---
    # 不配置时：日报生成/看板完全可用（本地 CSV 数据源），
    # 只有「同步到飞书」这一步返回 503 提示补配置。
    # 不能像 LLM_API_KEY 那样在启动时强制校验，否则没接飞书的人服务都起不来。
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_bitable_token: str = ""
    feishu_table_id: str = ""
    # --- 每日流水线 ---
    pipeline_enabled: bool = True
    pipeline_schedule: str = "09:00"
    # 流水线里是否调大模型做经营解读：默认关 —— 规则预警已可解释，
    # LLM 解读是加分项不是必需品，开着就是每天固定烧 token
    pipeline_use_llm: bool = False
    pipeline_refresh_data: bool = True
    # --- 批量任务 ---
    # 单次提交的行数上限：防止一次贴几千行把模型额度打满
    batch_max_rows: int = 200
    # 并发度：太高触发厂商限流，太低批量就没有意义
    batch_concurrency: int = 3

    @property
    def feishu_configured(self) -> bool:
        """飞书四件套是否齐备。同步端点据此决定 503 还是放行。"""
        return all(
            (self.feishu_app_id, self.feishu_app_secret,
             self.feishu_bitable_token, self.feishu_table_id)
        )


def load_settings(env_file: str | None = None) -> Settings:
    """读取共享配置 + 本包配置，一次性校验。

    共享部分复用 `ecom_shared.config.load_settings()`，本包只补自己的字段 ——
    不去复制一份厂商 preset 表，否则换厂商时要改两处。
    """
    from ecom_shared.config import load_settings as load_shared

    # 共享部分交给共享包读（provider preset、密钥、检索参数…），
    # 本包只补自己的字段 —— 换厂商时不用改两处。
    shared = load_shared(env_file)

    try:
        from datetime import datetime as _dt

        _dt.strptime(_env("PIPELINE_SCHEDULE", "09:00"), "%H:%M")
    except ValueError as exc:
        raise ConfigError(f"PIPELINE_SCHEDULE 必须是 HH:MM 格式（如 09:00）：{exc}") from exc

    batch_max_rows = _env_int("BATCH_MAX_ROWS", 200)
    batch_concurrency = _env_int("BATCH_CONCURRENCY", 3)
    if batch_max_rows < 1:
        raise ConfigError("BATCH_MAX_ROWS 必须 >= 1")
    if batch_concurrency < 1:
        raise ConfigError("BATCH_CONCURRENCY 必须 >= 1")

    return Settings(
        **{f: getattr(shared, f) for f in shared.__dataclass_fields__},
        data_dir=DATA_DIR,
        docs_dir=DOCS_DIR,
        import_dir=IMPORT_DIR,
        jobs_dir=DEFAULT_JOB_DIR,
        reports_dir=REPORT_DIR,
        sales_data_dir=_env("SALES_DATA_DIR") or SALES_DIR,
        feishu_app_id=_env("FEISHU_APP_ID"),
        feishu_app_secret=_env("FEISHU_APP_SECRET"),
        feishu_bitable_token=_env("FEISHU_BITABLE_APP_TOKEN"),
        feishu_table_id=_env("FEISHU_TABLE_ID"),
        pipeline_enabled=_env("PIPELINE_ENABLED", "true").lower() != "false",
        pipeline_schedule=_env("PIPELINE_SCHEDULE", "09:00"),
        pipeline_use_llm=_env("PIPELINE_USE_LLM", "false").lower() == "true",
        pipeline_refresh_data=_env("PIPELINE_REFRESH_DATA", "true").lower() != "false",
        batch_max_rows=batch_max_rows,
        batch_concurrency=batch_concurrency,
    )


__all__ = [
    "Settings",
    "load_settings",
    "REPO_ROOT",
    "PACKAGE_ROOT",
    "DATA_DIR",
    "DOCS_DIR",
    "IMPORT_DIR",
    "DEFAULT_JOB_DIR",
    "REPORT_DIR",
    "SALES_DIR",
    "STATE_PATH",
]
