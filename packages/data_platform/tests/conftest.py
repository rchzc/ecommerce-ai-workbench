"""测试公共 fixture。

**所有测试必须离线可跑**。日报 / 流水线 / 批量任务都可能在实现里偷偷调模型或
调飞书，所以这里用 session 级 autouse 把离线配置钉死，而不是指望每个用例自觉。
"""
from __future__ import annotations

import os

import pytest

from data_platform.config import Settings
from data_platform.report import ReportService
from tests.fixtures import make_settings


@pytest.fixture(scope="session", autouse=True)
def _offline_env():
    """钉死离线配置。session 级 + autouse：跑测试不可能碰到真厂商。"""
    keys = ("LLM_PROVIDER", "VECTOR_BACKEND", "LOG_LEVEL", "LLM_API_KEY", "FEISHU_APP_ID")
    saved = {k: os.environ.get(k) for k in keys}
    os.environ["LLM_PROVIDER"] = "mock"
    os.environ["VECTOR_BACKEND"] = "lexical"
    os.environ["LOG_LEVEL"] = "CRITICAL"
    os.environ.pop("LLM_API_KEY", None)
    os.environ.pop("FEISHU_APP_ID", None)
    yield
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


@pytest.fixture
def settings(tmp_path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def report_service(tmp_path) -> ReportService:
    return ReportService(make_settings(tmp_path))


@pytest.fixture(autouse=True)
def isolated_paths(tmp_path, monkeypatch):
    """把模块级路径常量改指到 tmp_path。**autouse，每个用例都生效。**

    `STATE_PATH` / `REPORT_DIR` 是模块级常量（不是实例字段），
    `ReportService.build()` 和 `PipelineService._save_state()` 直接读它们 ——
    不拦的话，跑一次测试就会往仓库的 `data/reports/` 和 `data/` 里写文件：
    测试"通过"了，但仓库被污染，而且第二次运行时残留状态会让断言莫名其妙地失败。

    所以这里不指望每个用例自觉声明，直接全局拦。
    """
    from data_platform import pipeline as ps
    from data_platform import report as rs

    monkeypatch.setattr(ps, "STATE_PATH", str(tmp_path / "pipeline_state.json"))
    monkeypatch.setattr(rs, "REPORT_DIR", str(tmp_path / "reports"))
    return tmp_path
