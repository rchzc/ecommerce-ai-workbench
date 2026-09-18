"""测试公共 fixture。

**所有测试必须离线可跑**。日报 / 流水线 / 批量任务都可能在实现里偷偷调模型或
调飞书，所以这里用 session 级 autouse 把离线配置钉死，而不是指望每个用例自觉。

同时钉掉路径类环境变量：用例一律用 `tests/fixtures.make_settings` 把
数据目录指向 tmp_path（服务的路径都从 Settings 取，所以配置一改就全隔离），
外部残留的 DATA_DIR 只会让"缺省值"指到别处、把断言搞成偶发失败。
"""
from __future__ import annotations

import os

import pytest

_ISOLATED_ENV_KEYS = (
    "LLM_PROVIDER",
    "VECTOR_BACKEND",
    "LOG_LEVEL",
    "LLM_API_KEY",
    "FEISHU_APP_ID",
    "DATA_DIR",
    "DOCS_DIR",
    "IMPORT_DIR",
    "JOBS_DIR",
    "REPORTS_DIR",
    "SALES_DATA_DIR",
    "STATE_PATH",
)

_OVERRIDES = {
    "LLM_PROVIDER": "mock",
    "VECTOR_BACKEND": "lexical",
    "LOG_LEVEL": "CRITICAL",  # 测试输出里不要混日志
}


@pytest.fixture(scope="session", autouse=True)
def _offline_env():
    """钉死离线配置。session 级 + autouse：跑测试不可能碰到真厂商。"""
    saved = {k: os.environ.get(k) for k in _ISOLATED_ENV_KEYS}
    for key in _ISOLATED_ENV_KEYS:
        os.environ.pop(key, None)
    os.environ.update(_OVERRIDES)
    yield
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
