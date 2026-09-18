"""飞书多维表格连接器测试：mock 模式走通链路、字段格式化、token 缓存。

真实网络调用不在单测覆盖范围（CI 无凭证），
_urlopen 层用 monkeypatch 替换验证认证与错误分支。
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from ecom_shared.errors import ConfigError

from data_platform.connectors.feishu_bitable import (
    FeishuBitableConnector,
    MockFeishuBitableConnector,
    format_report_row,
)
from data_platform.report import ReportService
from tests.fixtures import make_settings


class TestMockConnector:
    def test_mock_push_roundtrip(self):
        conn = MockFeishuBitableConnector()
        report = {
            "date": "2026-09-09",
            "metrics": {"revenue": 10975.0, "orders": 160, "units": 202},
            "alerts": [{"message": "[Amazon US] B0DEMO0002 库存不足"}],
            "markdown": "# 日报\n\n内容",
        }
        assert conn.push_daily_report(report)["created"] == 1

    def test_batch_create_empty_rows(self):
        assert MockFeishuBitableConnector().batch_create([]) == {"created": 0}


class TestFormatRow:
    def test_full_report_row(self):
        row = format_report_row(
            {
                "date": "2026-09-09",
                "metrics": {"revenue": 100.555, "orders": 10, "units": 12},
                "alerts": [{"message": "a"}, {"message": "b"}],
                "markdown": "x" * 9000,
            }
        )
        assert row["日期"] == "2026-09-09"
        assert row["GMV"] == 100.56  # round 保留两位
        assert row["订单数"] == 10
        assert row["预警数"] == 2
        assert row["预警摘要"] == "a；b"
        assert len(row["日报"]) == 5000  # 超长截断，防多维表格字段超限

    def test_empty_report_row(self):
        row = format_report_row({"date": "d", "metrics": {}, "alerts": [], "markdown": ""})
        assert row["GMV"] == 0
        assert row["预警摘要"] == "无"

    def test_alert_summary_capped_at_five(self):
        """预警摘要只取前 5 条：几十条预警全塞进一个单元格会顶到字段长度上限。"""
        row = format_report_row(
            {
                "date": "d",
                "metrics": {},
                "alerts": [{"message": f"m{i}"} for i in range(9)],
                "markdown": "",
            }
        )
        assert row["预警数"] == 9  # 数量如实统计
        assert row["预警摘要"] == "m0；m1；m2；m3；m4"  # 摘要只列前 5


class TestRealConnectorAuth:
    @staticmethod
    def _fake_response(payload: dict):
        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps(payload).encode()

        return Resp()

    def test_token_cached(self):
        conn = FeishuBitableConnector("id", "secret", "btok", "tbl")
        payload = {"code": 0, "tenant_access_token": "t-1", "expire": 7200}
        with patch("urllib.request.urlopen", return_value=self._fake_response(payload)) as mock_open:
            assert conn._tenant_token() == "t-1"
            assert conn._tenant_token() == "t-1"
            # token 未过期时不重复换发
            assert mock_open.call_count == 1

    def test_business_error_raises(self):
        conn = FeishuBitableConnector("id", "secret", "btok", "tbl")
        bad = {"code": 99991663, "msg": "app secret invalid", "tenant_access_token": ""}
        with patch("urllib.request.urlopen", return_value=self._fake_response(bad)):
            with pytest.raises(ValueError, match="tenant_access_token"):
                conn._tenant_token()

    def test_sync_without_config_fails_fast(self, tmp_path):
        """凭证没配时必须抛 ConfigError(503 语义)，而不是静默走 mock 假装成功。

        这条之前写成了 `pytest.raises((ExternalApiError, Exception))` ——
        元组里有 Exception，任何异常（甚至断言失败以外的一切）都能通过，
        等于没测。现在钉死具体异常类型与提示文案。
        """
        service = ReportService(make_settings(tmp_path, ""))
        report = {
            "date": "2026-09-09",
            "metrics": {"revenue": 1, "orders": 1, "units": 1},
            "alerts": [],
            "markdown": "m",
        }
        with pytest.raises(ConfigError, match="尚未配置飞书集成"):
            service.sync_to_feishu(report)  # type: ignore[arg-type]

    def test_http_error_maps_to_connection_error(self):
        import urllib.error

        conn = FeishuBitableConnector("id", "secret", "btok", "tbl")
        conn._token = "t"
        conn._token_expires_at = float("inf")

        def _raise(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 500, "boom", {}, None)  # type: ignore[arg-type]

        with patch("urllib.request.urlopen", side_effect=_raise):
            with pytest.raises(ConnectionError, match="HTTP 500"):
                conn._post("/bitable/v1/apps/x/tables/t/records/batch_create", {"records": []})
