"""流水线测试：幂等判重、数据刷新、四步编排、状态持久化、调度时间解析。

用 MockFeishuBitableConnector + 临时 CSV 走真实链路，不 mock 服务内部。
"""
from __future__ import annotations

import os

import pytest

from app.config import Settings
from app.connectors.feishu_bitable import MockFeishuBitableConnector
from app.services.pipeline_service import PipelineService, parse_schedule
from app.services.report_service import ReportService
from tests.test_report_service import BASE_ROWS, make_settings as make_report_settings


def make_settings(tmp_path, csv_text: str = BASE_ROWS, **overrides) -> Settings:
    s = make_report_settings(tmp_path, csv_text)
    return Settings(**{**s.__dict__, **overrides})


@pytest.fixture()
def service(tmp_path, monkeypatch):
    # 状态文件指到 tmp，避免污染真实 data/
    from app.services import pipeline_service as ps

    monkeypatch.setattr(ps, "STATE_PATH", str(tmp_path / "pipeline_state.json"))
    settings = make_settings(tmp_path)
    report = ReportService(settings, feishu=MockFeishuBitableConnector())
    return PipelineService(settings, report_service=report)


class TestSchedule:
    def test_parse_valid(self):
        assert parse_schedule("09:00") == (9, 0)
        assert parse_schedule("23:59") == (23, 59)

    def test_parse_invalid(self):
        with pytest.raises(ValueError):
            parse_schedule("9点")
        with pytest.raises(ValueError):
            parse_schedule("25:00")


class TestPipelineRun:
    @pytest.mark.asyncio
    async def test_full_run_ok(self, service, tmp_path):
        # BASE_ROWS 最新日期是 2026-09-02（过去），流水线应刷新出"今天"的行再同步
        result = await service.run(force=True)
        assert result["status"] == "ok"
        assert result["steps"]["refresh"]["status"] == "ok"
        assert result["steps"]["refresh"]["mode"] == "mock"
        assert result["steps"]["report"]["status"] == "ok"
        assert result["steps"]["feishu"]["status"] == "ok"
        assert result["steps"]["feishu"]["rows_created"] == 1
        # 刷新出的行已落盘 pipeline_rows.csv
        sales_dir = service.settings.sales_data_dir
        assert os.path.isfile(os.path.join(sales_dir, "pipeline_rows.csv"))
        # 状态文件已持久化
        assert service.last_run["status"] == "ok"

    @pytest.mark.asyncio
    async def test_idempotent_skip(self, service):
        first = await service.run(force=True)
        assert first["status"] == "ok"
        # 当日已成功：不 force 就跳过，不再写飞书
        second = await service.run()
        assert second.get("skipped") is True
        assert "幂等" in second["reason"]

    @pytest.mark.asyncio
    async def test_state_restored_on_new_instance(self, service, tmp_path):
        await service.run(force=True)
        # 新实例从状态文件恢复，同样幂等跳过 —— 重启服务不会重复同步
        settings = make_settings(tmp_path)
        report = ReportService(settings, feishu=MockFeishuBitableConnector())
        fresh = PipelineService(settings, report_service=report)
        result = await fresh.run()
        assert result.get("skipped") is True

    @pytest.mark.asyncio
    async def test_refresh_is_deterministic(self, service):
        """同一天两次强制重跑，模拟导出的数字必须一致（按日期做种子）。"""
        r1 = await service.run(force=True)
        rows1 = open(
            os.path.join(service.settings.sales_data_dir, "pipeline_rows.csv"), encoding="utf-8"
        ).read()
        os.remove(os.path.join(service.settings.sales_data_dir, "pipeline_rows.csv"))
        r2 = await service.run(force=True)
        rows2 = open(
            os.path.join(service.settings.sales_data_dir, "pipeline_rows.csv"), encoding="utf-8"
        ).read()
        assert rows1 == rows2
        assert r1["steps"]["report"]["date"] == r2["steps"]["report"]["date"]

    @pytest.mark.asyncio
    async def test_feishu_failure_recorded_not_raised(self, tmp_path, monkeypatch):
        from unittest.mock import MagicMock

        from app.errors import ExternalApiError

        settings = make_settings(tmp_path)
        report = ReportService(settings, feishu=MockFeishuBitableConnector())
        monkeypatch.setattr(
            report, "sync_to_feishu", MagicMock(side_effect=ExternalApiError("飞书炸了"))
        )
        from app.services import pipeline_service as ps

        monkeypatch.setattr(ps, "STATE_PATH", str(tmp_path / "state.json"))
        pipe = PipelineService(settings, report_service=report)
        result = await pipe.run(force=True)
        assert result["status"] == "failed"
        assert "飞书" in result["steps"]["feishu"]["error"]
        # 失败也要留痕：第二天调度不会因为今天失败而误判"已同步"
        assert pipe.last_run["status"] == "failed"

    @pytest.mark.asyncio
    async def test_refresh_disabled(self, tmp_path, monkeypatch):
        from app.services import pipeline_service as ps

        monkeypatch.setattr(ps, "STATE_PATH", str(tmp_path / "state.json"))
        settings = make_settings(tmp_path, pipeline_refresh_data=False)
        report = ReportService(settings, feishu=MockFeishuBitableConnector())
        pipe = PipelineService(settings, report_service=report)
        result = await pipe.run(force=True)
        assert result["steps"]["refresh"]["status"] == "skipped"
        # 没刷新就同步了最近可用日期（2026-09-02）的日报
        assert result["steps"]["report"]["date"] == "2026-09-02"
