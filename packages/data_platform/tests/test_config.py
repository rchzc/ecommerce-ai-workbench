"""配置与路径常量测试。

本文件守的是这次重构最核心的一条约定：**路径常量只有一个来源**。
以前每个模块自己按"文件在目录树里的深度"算 BACKEND_DIR，包一搬位置就
静默指到别的目录（不报错，只是读不到数据）。所以这里除了测值，还加了
一条结构性守卫，防止将来有人顺手把那行写回去。
"""
from __future__ import annotations

import dataclasses
import os
import re
import subprocess
import sys

import pytest
from ecom_shared.config import Settings as SharedSettings
from ecom_shared.errors import ConfigError

from data_platform import config as cfg
from data_platform.config import Settings, load_settings


class TestPathConstants:
    def test_defaults_live_under_package_root(self):
        """默认数据目录必须落在包自己的目录下 —— 这样 clone 下来就自带语料。"""
        assert cfg.DATA_DIR.startswith(cfg.PACKAGE_ROOT)
        for path in (cfg.DOCS_DIR, cfg.IMPORT_DIR, cfg.DEFAULT_JOB_DIR,
                     cfg.REPORT_DIR, cfg.SALES_DIR, cfg.STATE_PATH):
            assert path.startswith(cfg.DATA_DIR), path

    def test_state_file_is_inside_data_dir(self):
        assert os.path.dirname(cfg.STATE_PATH) == cfg.DATA_DIR
        assert os.path.basename(cfg.STATE_PATH) == "pipeline_state.json"

    def test_env_override_moves_everything(self):
        """改一个 DATA_DIR，所有子目录跟着走 —— 这是"单一来源"的实际收益。

        用**子进程**而不是 importlib.reload：reload 会生成一个新的 Settings 类，
        而其它模块早就持有了旧类的引用，`isinstance` 与 dataclasses.replace
        随之失效，污染整轮测试。子进程天然隔离。
        """
        code = (
            "import os;"
            "from data_platform import config as c;"
            "print(c.DATA_DIR);print(c.DOCS_DIR);print(c.REPORT_DIR);print(c.STATE_PATH)"
        )
        # 子进程不继承 sys.path，得显式带上 —— 直接复用当前解释器的路径，
        # 免得手工拼出来的路径跟 pytest 实际用的那套不一致（那种失败最难查）。
        env = dict(
            os.environ,
            DATA_DIR=r"D:\tmp\vol",
            PYTHONPATH=os.pathsep.join(p for p in sys.path if p),
            PYTHONIOENCODING="utf-8",
        )
        out = subprocess.run(
            [sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60
        )
        assert out.returncode == 0, out.stderr
        data_dir, docs, reports, state = out.stdout.strip().splitlines()
        assert data_dir == r"D:\tmp\vol"
        assert docs == os.path.join(r"D:\tmp\vol", "docs")
        assert reports == os.path.join(r"D:\tmp\vol", "reports")
        assert state == os.path.join(r"D:\tmp\vol", "pipeline_state.json")

    def test_no_module_recomputes_backend_dir(self):
        """结构性守卫：包内任何模块都不许再出现"连续三次取上级目录"的写法。

        这个 bug 的特点是"错了也不报错"，功能测试抓不稳 ——
        所以在源码层直接禁掉这个模式。
        """
        pattern = re.compile(r"dirname\(\s*os\.path\.dirname\(\s*os\.path\.dirname")
        offenders = [
            os.path.relpath(os.path.join(dirpath, name), cfg.PACKAGE_ROOT)
            for dirpath, _dirs, files in os.walk(cfg.PACKAGE_ROOT)
            for name in files
            if name.endswith(".py")
            and pattern.search(open(os.path.join(dirpath, name), encoding="utf-8").read())
        ]
        assert offenders == [], f"这些模块又自己按目录深度算路径了：{offenders}"

    def test_bundled_corpus_present(self):
        """自带语料不能是空壳：连接器写实时数据、RAG 读它，缺了演示就断链。"""
        assert os.path.isdir(cfg.DOCS_DIR), "data/docs 目录缺失"
        domains = sorted(
            d for d in os.listdir(cfg.DOCS_DIR)
            if os.path.isdir(os.path.join(cfg.DOCS_DIR, d))
        )
        assert len(domains) == 6, domains
        md = [
            f for d in domains
            for f in os.listdir(os.path.join(cfg.DOCS_DIR, d))
            if f.endswith(".md")
        ]
        assert len(md) == 26, f"语料篇数应为 26，实际 {len(md)}"


class TestSettingsShape:
    def test_extends_shared_settings(self):
        """业务 Settings 必须继承共享 Settings —— 共享层与业务层的分层契约。"""
        assert issubclass(Settings, SharedSettings)

    def test_contains_all_shared_fields(self):
        shared = set(SharedSettings.__dataclass_fields__)
        own = set(Settings.__dataclass_fields__)
        assert shared <= own, f"业务层丢了共享字段：{shared - own}"

    def test_frozen(self):
        """不可变：运行期没人能偷偷把 provider 换成真厂商。"""
        with pytest.raises(dataclasses.FrozenInstanceError):
            load_settings().provider = "openai"  # type: ignore[misc]


class TestLoadSettings:
    def test_merges_shared_and_package_fields(self):
        s = load_settings()
        assert s.provider == "mock"          # 来自共享层
        assert s.docs_dir == cfg.DOCS_DIR    # 来自本包
        assert s.batch_max_rows >= 1

    def test_bad_schedule_raises(self, monkeypatch):
        monkeypatch.setenv("PIPELINE_SCHEDULE", "9点")
        with pytest.raises(ConfigError, match="HH:MM"):
            load_settings()

    def test_bad_int_raises(self, monkeypatch):
        monkeypatch.setenv("BATCH_MAX_ROWS", "many")
        with pytest.raises(ConfigError, match="BATCH_MAX_ROWS"):
            load_settings()

    def test_non_positive_int_raises(self, monkeypatch):
        monkeypatch.setenv("BATCH_CONCURRENCY", "0")
        with pytest.raises(ConfigError, match="BATCH_CONCURRENCY"):
            load_settings()

    def test_feishu_configured_flag(self, monkeypatch):
        assert load_settings().feishu_configured is False
        monkeypatch.setenv("FEISHU_APP_ID", "id")
        monkeypatch.setenv("FEISHU_APP_SECRET", "secret")
        monkeypatch.setenv("FEISHU_BITABLE_APP_TOKEN", "btok")
        monkeypatch.setenv("FEISHU_TABLE_ID", "tbl")
        assert load_settings().feishu_configured is True

    def test_partial_feishu_config_is_not_configured(self, monkeypatch):
        """四件套缺一不可：只配了一半时同步端点仍应快速失败，不能半通。"""
        monkeypatch.setenv("FEISHU_APP_ID", "id")
        monkeypatch.setenv("FEISHU_APP_SECRET", "secret")
        assert load_settings().feishu_configured is False
