"""流水线测试：幂等判重、数据刷新、四步编排、状态持久化、调度时间解析。

用 MockFeishuBitableConnector + 临时 CSV 走真实链路，不 mock 服务内部。

**路径隔离靠 `make_settings`，不靠 monkeypatch 模块常量。**
状态文件与报表目录现在都从 `Settings` 取（`state_path` / `reports_dir`），
所以只要配置指向 tmp_path，用例之间就天然互不干扰 ——
不用再往仓库的 `data/` 里写文件，也不需要"全局打补丁"那种脆弱做法。

**异步驱动统一用 `asyncio.run`，不引 pytest-asyncio。**
这些用例要的只是"把协程跑到结束"，为此多一个测试插件不值得：
插件版本一旦和 pytest 主版本错位，失败信息会指向插件而不是被测代码，
排查成本远高于收益。同一仓库里两种写法混着用更糟 —— 所以全仓统一。
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
from unittest.mock import MagicMock

import pytest
from ecom_shared.errors import ExternalApiError

from data_platform.config import Settings
from data_platform.connectors.feishu_bitable import MockFeishuBitableConnector
from data_platform.pipeline import PipelineService, parse_schedule
from data_platform.report import ReportService
from tests.fixtures import BASE_ROWS, make_settings as make_report_settings


def make_settings(tmp_path, csv_text: str = BASE_ROWS, **overrides) -> Settings:
    return dataclasses.replace(make_report_settings(tmp_path, csv_text), **overrides)


def build_pipeline(tmp_path, **overrides) -> PipelineService:
    settings = make_settings(tmp_path, **overrides)
    report = ReportService(settings, feishu=MockFeishuBitableConnector())
    return PipelineService(settings, report_service=report)


def run(coro):
    return asyncio.run(coro)


class TestSchedule:
    def test_parse_valid(self):
        assert parse_schedule("09:00") == (9, 0)
        assert parse_schedule("23:59") == (23, 59)

    def test_parse_invalid(self):
        with pytest.raises(ValueError):
            parse_schedule("9点")
        with pytest.raises(ValueError):
            parse_schedule("25:00")

    def test_parse_rejects_missing_minute(self):
        with pytest.raises(ValueError):
            parse_schedule("09")


class TestPipelineRun:
    def test_full_run_ok(self, tmp_path):
        service = build_pipeline(tmp_path)
        # BASE_ROWS 最新日期是 2026-09-02（过去），流水线应刷新出"今天"的行再同步
        result = run(service.run(force=True))
        assert result["status"] == "ok"
        assert result["steps"]["refresh"]["status"] == "ok"
        assert result["steps"]["refresh"]["mode"] == "mock"
        assert result["steps"]["report"]["status"] == "ok"
        assert result["steps"]["feishu"]["status"] == "ok"
        assert result["steps"]["feishu"]["rows_created"] == 1
        # 刷新出的行已落盘 pipeline_rows.csv
        assert os.path.isfile(os.path.join(service.settings.sales_data_dir, "pipeline_rows.csv"))
        # 状态文件已持久化
        assert service.last_run["status"] == "ok"

    def test_idempotent_skip(self, tmp_path):
        service = build_pipeline(tmp_path)
        assert run(service.run(force=True))["status"] == "ok"
        # 当日已成功：不 force 就跳过，不再写飞书
        second = run(service.run())
        assert second.get("skipped") is True
        assert "幂等" in second["reason"]

    def test_state_restored_on_new_instance(self, tmp_path):
        run(build_pipeline(tmp_path).run(force=True))
        # 新实例从状态文件恢复，同样幂等跳过 —— 重启服务不会重复同步
        assert run(build_pipeline(tmp_path).run()).get("skipped") is True

    def test_refresh_is_deterministic(self, tmp_path):
        """同一天两次强制重跑，模拟导出的数字必须一致（按日期做种子）。"""
        service = build_pipeline(tmp_path)
        rows_path = os.path.join(service.settings.sales_data_dir, "pipeline_rows.csv")
        r1 = run(service.run(force=True))
        rows1 = open(rows_path, encoding="utf-8").read()
        os.remove(rows_path)
        r2 = run(service.run(force=True))
        rows2 = open(rows_path, encoding="utf-8").read()
        assert rows1 == rows2
        assert r1["steps"]["report"]["date"] == r2["steps"]["report"]["date"]

    def test_feishu_failure_recorded_not_raised(self, tmp_path):
        pipe = build_pipeline(tmp_path)
        pipe.report.sync_to_feishu = MagicMock(side_effect=ExternalApiError("飞书炸了"))
        result = run(pipe.run(force=True))
        assert result["status"] == "failed"
        assert "飞书" in result["steps"]["feishu"]["error"]
        # 失败也要留痕：第二天调度不会因为今天失败而误判"已同步"
        assert pipe.last_run["status"] == "failed"

    def test_refresh_disabled(self, tmp_path):
        pipe = build_pipeline(tmp_path, pipeline_refresh_data=False)
        result = run(pipe.run(force=True))
        assert result["steps"]["refresh"]["status"] == "skipped"
        # 没刷新就同步了最近可用日期（2026-09-02）的日报
        assert result["steps"]["report"]["date"] == "2026-09-02"

    def test_force_recovers_after_failure(self, tmp_path):
        """失败留痕之后 force 重跑必须能恢复成功 —— 否则一次飞书抖动就永久卡死。"""
        pipe = build_pipeline(tmp_path)
        pipe.report.sync_to_feishu = MagicMock(side_effect=ExternalApiError("飞书炸了"))
        assert run(pipe.run(force=True))["status"] == "failed"

        pipe.report.sync_to_feishu = MagicMock(return_value={"created": 1})
        assert run(pipe.run(force=True))["status"] == "ok"


class TestStateFile:
    def test_state_file_follows_settings(self, tmp_path):
        """状态文件位置来自 Settings.state_path，不是模块常量。

        这条很实在：模块常量是 import 时的快照，宿主应用先 import 再设
        DATA_DIR 就失效了 —— 挂载卷上的状态文件会"看起来在写、其实写别处"。
        """
        pipe = build_pipeline(tmp_path)
        assert pipe.state_path == str(tmp_path / "pipeline_state.json")
        run(pipe.run(force=True))
        assert os.path.isfile(pipe.state_path)

    def test_custom_state_path_is_honored(self, tmp_path):
        custom = tmp_path / "vol" / "state.json"
        pipe = build_pipeline(tmp_path, state_path=str(custom))
        run(pipe.run(force=True))
        assert custom.is_file()

    def test_corrupt_state_file_does_not_crash(self, tmp_path):
        """状态文件被写坏时应当作"没有历史状态"继续跑，不能启动就崩。"""
        broken = tmp_path / "bad.json"
        broken.write_text("{ not json", encoding="utf-8")
        assert build_pipeline(tmp_path, state_path=str(broken)).last_run == {}
