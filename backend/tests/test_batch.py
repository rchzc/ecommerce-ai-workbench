"""批量任务、规则校验与表格读写的测试。

用 asyncio.run 驱动而不是引入 pytest-asyncio：
    这几处需要的是"把协程跑到结束"，不值得为此多一个测试依赖。
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.core.tabular import parse_csv, to_csv  # noqa: E402
from app.core.validators import PASS, FAIL, WARN, validate_output  # noqa: E402
from app.errors import NotFoundError, ValidationError  # noqa: E402
from app.services.batch_service import (  # noqa: E402
    STATUS_DONE,
    STATUS_PARTIAL,
    BatchService,
)

LISTING_SCHEMA = {
    "title": "string，产品标题",
    "bullets": ["string，五点描述"],
    "keywords": {"core": ["string"], "long_tail": ["string"]},
}


class _FakeAgentService:
    """替身：不碰真实模型，能按需让指定行失败。"""

    def __init__(self, fail_on: set[int] | None = None) -> None:
        self.fail_on = fail_on or set()
        self.calls = 0

    def output_schema(self, agent_name: str) -> dict:
        if agent_name != "listing":
            raise NotFoundError(f"未知 Agent: {agent_name}")
        return LISTING_SCHEMA

    async def run(self, agent_name: str, payload: dict) -> dict:
        self.calls += 1
        index = int(payload.get("__index", 0))
        if index in self.fail_on:
            from app.errors import ModelCallError

            raise ModelCallError("模拟模型调用失败")
        return {
            "agent": agent_name,
            "domain": agent_name,
            "data": {
                "title": f"{payload.get('product', '商品')} 标题",
                "bullets": ["卖点一", "卖点二"],
                "keywords": {"core": ["a"], "long_tail": ["b"]},
            },
            "knowledge": [{"source": "listing.md", "text": "", "domain": "listing", "score": 0.9}],
            "meta": {"model": "fake"},
        }


async def _wait_done(service: BatchService, job_id: str, timeout: float = 3.0) -> object:
    """轮询等待任务终态。"""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        job = service.get_job(job_id)
        if job.status not in ("pending", "running"):
            return job
        await asyncio.sleep(0.01)
    raise AssertionError("任务未在超时时间内完成")


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- 规则校验
def test_validate_complete_output_passes():
    data = {
        "title": "轻便折叠伞",
        "bullets": ["卖点一", "卖点二"],
        "keywords": {"core": ["umbrella"], "long_tail": ["fold umbrella"]},
    }
    result = validate_output(data, LISTING_SCHEMA)
    assert result.status == PASS
    assert result.issues == []


def test_validate_missing_field_warns():
    data = {"title": "轻便折叠伞", "bullets": ["卖点一"], "keywords": {}}
    result = validate_output(data, LISTING_SCHEMA)
    assert result.status == WARN
    assert any(i.rule == "missing_field" and i.field == "keywords" for i in result.issues)


def test_validate_placeholder_is_detected():
    """占位内容是"看起来有值"的空结果，必须被单独识别出来。"""
    data = {
        "title": "N/A",
        "bullets": ["待补充"],
        "keywords": {"core": ["a"], "long_tail": ["b"]},
    }
    result = validate_output(data, LISTING_SCHEMA)
    assert result.status == WARN
    assert any(i.rule == "placeholder" for i in result.issues)


def test_validate_type_mismatch_warns():
    data = {
        "title": "轻便折叠伞",
        "bullets": "本该是数组却给了字符串",
        "keywords": {"core": ["a"], "long_tail": ["b"]},
    }
    result = validate_output(data, LISTING_SCHEMA)
    assert any(i.rule == "type_mismatch" for i in result.issues)


def test_validate_collapsed_structure_fails():
    """缺失过半字段 = 输出结构整体崩塌，不是个别字段问题，必须整体判 fail。"""
    result = validate_output({"title": "只有一个字段"}, LISTING_SCHEMA)
    assert result.status == FAIL
    assert any(i.rule == "structure_collapsed" for i in result.issues)


def test_validate_empty_output_fails():
    result = validate_output({}, LISTING_SCHEMA)
    assert result.status == FAIL
    assert result.issues[0].rule == "empty_output"


# ---------------------------------------------------------------- CSV 读写
def test_parse_csv_basic():
    text = "商品,类目\n折叠伞,户外\n水杯,家居\n"
    rows = parse_csv(text)
    assert rows == [{"商品": "折叠伞", "类目": "户外"}, {"商品": "水杯", "类目": "家居"}]


def test_parse_csv_handles_bom_and_blank_lines():
    text = "\ufeff商品,类目\n折叠伞,户外\n\n\n"
    rows = parse_csv(text)
    assert len(rows) == 1
    assert rows[0]["商品"] == "折叠伞"  # BOM 未污染首列表头


def test_parse_csv_dedupes_header():
    text = "关键词,关键词\n防晒,折叠\n"
    rows = parse_csv(text)
    assert list(rows[0].keys()) == ["关键词", "关键词_1"]


def test_to_csv_unions_columns():
    rows = [{"a": "1"}, {"b": "2"}]
    csv_text = to_csv(rows)
    assert csv_text.startswith("\ufeff")  # Excel 中文不乱码
    assert "a,b" in csv_text


# ---------------------------------------------------------------- 批量任务
def test_batch_runs_every_row(tmp_path):
    async def main():
        svc = BatchService(agent_service=_FakeAgentService(), job_dir=str(tmp_path))
        job = svc.create_job("listing", [{"product": "A"}, {"product": "B"}, {"product": "C"}])
        job = await _wait_done(svc, job.job_id)
        assert job.status == STATUS_DONE
        assert job.total == 3 and job.ok_count == 3
        assert all(r.validation and r.validation["status"] == PASS for r in job.rows)

    _run(main())


def test_batch_single_row_failure_does_not_break_others(tmp_path):
    async def main():
        # 第 2 行（index=1）失败
        svc = BatchService(
            agent_service=_FakeAgentService(fail_on={1}), job_dir=str(tmp_path)
        )
        job = svc.create_job(
            "listing",
            [{"__index": 0, "product": "A"}, {"__index": 1, "product": "B"}, {"__index": 2, "product": "C"}],
        )
        job = await _wait_done(svc, job.job_id)
        assert job.status == STATUS_PARTIAL  # 部分成功，不是整批失败
        assert job.ok_count == 2 and job.failed_count == 1
        failed = [r for r in job.rows if r.status == "failed"]
        assert failed and "model_call_error" in failed[0].error  # 失败原因可定位

    _run(main())


def test_batch_idempotency_key_prevents_duplicate_calls(tmp_path):
    async def main():
        fake = _FakeAgentService()
        svc = BatchService(agent_service=fake, job_dir=str(tmp_path))
        first = svc.create_job("listing", [{"product": "A"}], idempotency_key="k1")
        second = svc.create_job("listing", [{"product": "A"}], idempotency_key="k1")
        await _wait_done(svc, first.job_id)
        assert first.job_id == second.job_id
        assert fake.calls == 1  # 重复提交没有触发第二次模型调用

    _run(main())


def test_batch_rejects_too_many_rows(tmp_path):
    svc = BatchService(agent_service=_FakeAgentService(), max_rows=2, job_dir=str(tmp_path))
    with pytest.raises(ValidationError):
        svc.create_job("listing", [{"a": "1"}, {"a": "2"}, {"a": "3"}])


def test_batch_rejects_empty_rows(tmp_path):
    svc = BatchService(agent_service=_FakeAgentService(), job_dir=str(tmp_path))
    with pytest.raises(ValidationError):
        svc.create_job("listing", [])


def test_batch_unknown_agent_fails_fast(tmp_path):
    """未知 Agent 要在提交时就报 404，而不是等到后台任务里全军覆没。"""
    svc = BatchService(agent_service=_FakeAgentService(), job_dir=str(tmp_path))
    with pytest.raises(NotFoundError):
        svc.create_job("not_exist", [{"a": "1"}])


def test_batch_export_rows_flatten_output(tmp_path):
    async def main():
        svc = BatchService(agent_service=_FakeAgentService(), job_dir=str(tmp_path))
        job = svc.create_job("listing", [{"product": "折叠伞"}])
        job = await _wait_done(svc, job.job_id)
        rows = svc.export_rows(job)
        assert rows[0]["product"] == "折叠伞"  # 输入列原样保留
        assert rows[0]["out.title"] == "折叠伞 标题"  # 输出列统一前缀
        assert rows[0]["校验结果"] == "通过"
        assert rows[0]["知识来源"] == "listing.md"

    _run(main())


def test_batch_persists_and_restores(tmp_path):
    """重启后任务仍可查：这是"业务系统"和"内存 Demo"的区别。"""
    async def main():
        svc = BatchService(agent_service=_FakeAgentService(), job_dir=str(tmp_path))
        job = svc.create_job("listing", [{"product": "A"}])
        await _wait_done(svc, job.job_id)
        return job.job_id

    job_id = _run(main())

    restored = BatchService(agent_service=_FakeAgentService(), job_dir=str(tmp_path))
    assert restored.get_job(job_id).total == 1
