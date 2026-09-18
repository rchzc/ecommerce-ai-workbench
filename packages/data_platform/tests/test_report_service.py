"""日报服务测试：聚合正确性、环比口径、四条预警规则、markdown 与落盘。

原则：不 mock 服务层内部逻辑，用真实 CSV 文件（tmp_path 生成）走完整链路 ——
这类纯计算服务 mock 内部等于把断言写给自己看。
"""
from __future__ import annotations

import dataclasses
import os

import pytest
from ecom_shared.errors import ConfigError, NotFoundError, ValidationError

from data_platform.report import LEVEL_HIGH, LEVEL_MID, ReportService
from tests.fixtures import BASE_ROWS, CSV_HEAD, make_settings


@pytest.fixture()
def service(tmp_path):
    return ReportService(make_settings(tmp_path))


class TestAggregation:
    def test_latest_date_and_metrics(self, service):
        report = service.build()
        assert report.date == "2026-09-02"
        m = report.metrics
        assert m["revenue"] == pytest.approx(2700.0)
        assert m["orders"] == 27
        assert m["units"] == 43
        assert m["shops"] == 2
        assert m["skus"] == 2

    def test_explicit_date(self, service):
        report = service.build("2026-09-01")
        assert report.metrics["revenue"] == pytest.approx(2300.0)

    def test_by_shop_sorted_by_revenue(self, service):
        report = service.build()
        shops = [s["shop"] for s in report.by_shop]
        assert shops[0] == "Amazon US"  # 1800 > 900
        assert len(report.by_shop) == 2

    def test_trend_tail(self, service):
        report = service.build()
        assert [t["date"] for t in report.trend] == ["2026-09-01", "2026-09-02"]
        assert report.trend[1]["revenue"] == pytest.approx(2700.0)

    def test_markdown_contains_key_sections(self, service):
        report = service.build()
        assert "# 跨境电商销售日报 · 2026-09-02" in report.markdown
        assert "## 核心指标" in report.markdown
        assert "## 分店铺" in report.markdown

    def test_persist_to_reports_dir(self, service, tmp_path, monkeypatch):
        from data_platform import report as rs

        monkeypatch.setattr(rs, "REPORT_DIR", str(tmp_path / "reports"))
        report = service.build()
        assert report.saved_to
        assert os.path.isfile(report.saved_to)
        assert "2026-09-02" in open(report.saved_to, encoding="utf-8").read()

    def test_persist_failure_does_not_block_report(self, service, tmp_path, monkeypatch):
        """落盘失败只记日志：看板照样要能渲染，不能因为磁盘问题整页 500。

        用一个**文件**当报表目录：`os.makedirs` 会抛 FileExistsError(OSError)，
        正好命中 `_persist` 的降级分支。用含空字符的路径不行 ——
        那抛的是 ValueError，压根进不了 OSError 分支，测了个假的。
        """
        from data_platform import report as rs

        blocker = tmp_path / "blocked"
        blocker.write_text("not a dir", encoding="utf-8")
        monkeypatch.setattr(rs, "REPORT_DIR", str(blocker))
        report = service.build()
        assert report.date == "2026-09-02"
        assert report.saved_to == ""


class TestAlerts:
    def test_no_alerts_on_healthy_data(self, service):
        assert service.build().alerts == []

    def test_stock_below_safe_triggers(self, tmp_path):
        rows = BASE_ROWS + "2026-09-02,Amazon US,B0C,3,3,300.0,30.0,40,100,6,0\n"
        report = ReportService(make_settings(tmp_path, rows)).build()
        stock = [a for a in report.alerts if a["type"] == "stock"]
        assert len(stock) == 1
        assert stock[0]["level"] == LEVEL_HIGH
        assert "B0C" in stock[0]["message"] and "低于安全水位" in stock[0]["message"]

    def test_revenue_drop_triggers(self, tmp_path):
        # 09-03 GMV 从 2700 跌到 1500（-44% > 30%）
        rows = (
            BASE_ROWS
            + "2026-09-03,Amazon US,B0A,5,10,500.0,60.0,460,150,5,0\n"
            + "2026-09-03,Amazon US,B0B,4,4,400.0,40.0,560,150,5,0\n"
            + "2026-09-03,Shopify,B0A,6,6,600.0,50.0,660,150,5,0\n"
        )
        report = ReportService(make_settings(tmp_path, rows)).build()
        drops = [a for a in report.alerts if a["type"] == "revenue_drop"]
        assert drops, "GMV 环比跌 44% 必须触发预警"
        assert drops[0]["level"] == LEVEL_MID

    def test_bad_review_ratio_triggers(self, tmp_path):
        # B0D 当日差评率 2/6 = 33% > 10%
        rows = BASE_ROWS + "2026-09-02,Shopify,B0D,4,4,400.0,40.0,900,150,6,2\n"
        report = ReportService(make_settings(tmp_path, rows)).build()
        bad = [a for a in report.alerts if a["type"] == "bad_reviews"]
        assert bad and "差评率" in bad[0]["message"]

    def test_ad_spend_ratio_triggers(self, tmp_path):
        # Shopify 当日广告 500 / GMV 900 = 55% > 30%（单独一行覆盖该店铺）
        rows = (
            CSV_HEAD
            + "2026-09-01,Shopify,B0A,8,12,800.0,80.0,700,150,10,1\n"
            + "2026-09-02,Shopify,B0A,9,13,900.0,500.0,680,150,10,1\n"
            + "2026-09-02,Amazon US,B0A,12,24,1200.0,110.0,480,150,10,1\n"
            + "2026-09-02,Amazon US,B0B,6,6,600.0,60.0,580,150,10,0\n"
        )
        report = ReportService(make_settings(tmp_path, rows)).build()
        ad = [a for a in report.alerts if a["type"] == "ad_spend"]
        assert ad and "广告" in ad[0]["message"]
        assert ad[0]["message"].startswith("[Shopify]")

    def test_alerts_sorted_high_first(self, tmp_path):
        rows = (
            BASE_ROWS
            + "2026-09-02,Shopify,B0E,2,2,200.0,100.0,10,50,6,2\n"  # 库存 + 差评
            + "2026-09-02,Amazon US,B0F,1,1,100.0,10.0,5,50,6,2\n"
        )
        report = ReportService(make_settings(tmp_path, rows)).build()
        levels = [a["level"] for a in report.alerts]
        assert levels.count(LEVEL_HIGH) >= 1
        assert levels[0] == LEVEL_HIGH

    def test_alert_markdown_emphasis_not_broken(self, tmp_path):
        """markdown 里的加粗必须闭合在方括号内：`**[高]** 文案`。

        写错成 `**[高**] 文案` 时 CommonMark 不会渲染成粗体，
        但纯文本断言（只查子串）完全测不出来 —— 所以这里断言闭合位置。
        """
        rows = BASE_ROWS + "2026-09-02,Amazon US,B0C,3,3,300.0,30.0,40,100,6,0\n"
        md = ReportService(make_settings(tmp_path, rows)).build().markdown
        assert f"- **[{LEVEL_HIGH}]** " in md
        assert "**[" not in md.replace(f"**[{LEVEL_HIGH}]**", "")


class TestDirtyData:
    def test_missing_required_column_raises(self, tmp_path):
        bad = "date,shop,sku,orders\n2026-09-01,Amazon US,B0A,10\n"
        with pytest.raises(ValidationError):
            ReportService(make_settings(tmp_path, bad)).build()

    def test_dirty_rows_skipped_not_fatal(self, tmp_path):
        rows = (
            BASE_ROWS
            + "2026-09-02,Amazon US,B0X,not-a-number,x,abc,0,0,0,0,0\n"  # 脏行
            + ",Amazon US,B0Y,1,1,100,0,0,0,0,0\n"  # 缺日期
            + "2026-09-03,Amazon US,B0Z,1,1,100.0,0,0,0,0,0\n"  # 正常行
        )
        report = ReportService(make_settings(tmp_path, rows)).build()
        assert report.date == "2026-09-03"
        assert report.metrics["revenue"] == pytest.approx(100.0)

    def test_unknown_date_raises(self, service):
        with pytest.raises(NotFoundError):
            service.build("2030-01-01")

    def test_missing_dir_raises(self, tmp_path):
        s = make_settings(tmp_path, BASE_ROWS)
        bad = dataclasses.replace(s, sales_data_dir=str(tmp_path / "nope"))
        with pytest.raises(NotFoundError):
            ReportService(bad).build()


class TestFeishuSync:
    def test_sync_without_config_raises_503(self, service):
        report = service.build()
        with pytest.raises(ConfigError, match="尚未配置飞书集成"):
            service.sync_to_feishu(report)

    def test_sync_error_message_points_at_env_keys(self, service):
        """报错要能直接照做：把该填的四个环境变量名写进消息里。"""
        report = service.build()
        with pytest.raises(ConfigError) as exc:
            service.sync_to_feishu(report)
        for key in ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_BITABLE_APP_TOKEN", "FEISHU_TABLE_ID"):
            assert key in str(exc.value)
