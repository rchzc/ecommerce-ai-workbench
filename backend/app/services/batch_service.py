"""批量任务服务：把「一次问一条」变成「一次跑一批」。

为什么它是这个岗位需要的那类能力：
    运营手里的活天然是表格 —— 一批 SKU 要写 Listing、一批竞品要做分析、
    一批评论要打标签。逐个粘贴进对话框，本质上还是"人在用 AI"，
    人一走，效率就跟着走了。批量化 + 规则校验 + 结果回写表格，
    才把"AI 能力"变成"团队可重复调用的 SOP"。

三个关键工程决策：
1. **异步任务 + 轮询**，不在请求处理器里跑长任务。
   200 行跑几分钟，HTTP 请求早超时了；立即返回 job_id 让前端轮询才是正解。
2. **单行失败不影响整批**，失败行标记原因留在结果里。
   批量任务最怕"第 3 行失败导致 197 行白跑"，失败要可定位、可重跑。
3. **幂等键**：同一个 idempotency_key 只跑一次。
   n8n 重试、前端重复点击、网络重传都不该触发第二次模型调用 —— 那是在烧钱。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from ..core.tabular import flatten
from ..core.validators import validate_output
from ..errors import AppError, NotFoundError, ValidationError

logger = logging.getLogger(__name__)

# 任务保留时长：超过后惰性清理，避免内存和磁盘无限增长
JOB_TTL_SECONDS = 24 * 3600

# 任务状态
STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_PARTIAL = "partial"  # 部分成功
STATUS_FAILED = "failed"

# 行状态
ROW_OK = "ok"
ROW_FAILED = "failed"
ROW_PENDING = "pending"

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_JOB_DIR = os.path.join(BACKEND_DIR, "data", "jobs")

_ROW_LABELS = {ROW_OK: "成功", ROW_FAILED: "失败", ROW_PENDING: "处理中"}
_VALIDATION_LABELS = {"pass": "通过", "warn": "需留意", "fail": "需人工处理"}


@dataclass
class RowResult:
    """单行的执行结果。失败也保留在结果里，作为可定位、可重跑的"死信行"。"""

    index: int
    input: dict[str, Any]
    status: str = ROW_PENDING
    data: dict[str, Any] | None = None
    validation: dict[str, Any] | None = None
    sources: list[str] = field(default_factory=list)
    error: str | None = None
    elapsed_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "status": self.status,
            "input": self.input,
            "data": self.data,
            "validation": self.validation,
            "sources": self.sources,
            "error": self.error,
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass
class BatchJob:
    job_id: str
    agent: str
    created_at: float
    updated_at: float
    status: str = STATUS_PENDING
    idempotency_key: str | None = None
    error: str | None = None
    rows: list[RowResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.rows)

    @property
    def finished(self) -> int:
        return sum(1 for r in self.rows if r.status != ROW_PENDING)

    @property
    def ok_count(self) -> int:
        return sum(1 for r in self.rows if r.status == ROW_OK)

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.rows if r.status == ROW_FAILED)

    @property
    def warned_count(self) -> int:
        return sum(
            1 for r in self.rows if r.validation and r.validation.get("status") == "warn"
        )

    @property
    def failed_rows(self) -> int:
        return sum(
            1 for r in self.rows if r.validation and r.validation.get("status") == "fail"
        )

    @property
    def progress(self) -> float:
        """完成比例，前端进度条用。"""
        if self.total == 0:
            return 0.0
        return round(self.finished / self.total, 3)

    def to_dict(self, *, include_rows: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "job_id": self.job_id,
            "agent": self.agent,
            "status": self.status,
            "total": self.total,
            "finished": self.finished,
            "ok": self.ok_count,
            "failed": self.failed_count,
            "warned": self.warned_count,
            "need_review": self.failed_rows,
            "progress": self.progress,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if include_rows:
            payload["rows"] = [r.to_dict() for r in self.rows]
        return payload


class BatchService:
    """批量任务编排：创建任务、并发执行、结果落盘与导出。"""

    def __init__(
        self,
        *,
        agent_service: Any,
        max_rows: int = 200,
        concurrency: int = 3,
        job_dir: str = DEFAULT_JOB_DIR,
    ) -> None:
        self.agent_service = agent_service
        self.max_rows = max_rows
        self.concurrency = concurrency
        self.job_dir = job_dir
        self._jobs: dict[str, BatchJob] = {}
        self._idempotency: dict[str, str] = {}
        # 持有 task 引用：不保存的话 asyncio 可能在任务完成前把它回收掉
        self._tasks: set[asyncio.Task] = set()
        os.makedirs(self.job_dir, exist_ok=True)
        self._restore()

    # --- 对外接口 ------------------------------------------------------
    def create_job(
        self,
        agent: str,
        rows: list[dict[str, Any]],
        *,
        idempotency_key: str | None = None,
    ) -> BatchJob:
        """创建并启动一个批量任务。

        幂等语义：同一个 key 已提交过就直接返回原任务，绝不重复调用模型。
        即使原任务已失败也返回原结果 —— 要重跑请换一个 key，
        否则"重试"和"重复提交"就分不清了，防重复扣费也就无从谈起。
        """
        if idempotency_key:
            existing_id = self._idempotency.get(idempotency_key)
            if existing_id and existing_id in self._jobs:
                logger.info(
                    "batch.idempotent_hit",
                    extra={"key": idempotency_key, "job_id": existing_id},
                )
                return self._jobs[existing_id]

        if not rows:
            raise ValidationError("批量任务的行不能为空")
        if len(rows) > self.max_rows:
            raise ValidationError(
                f"单次批量任务最多 {self.max_rows} 行，本次提交 {len(rows)} 行，请拆分后重试"
            )
        for idx, row in enumerate(rows):
            if not isinstance(row, dict) or not row:
                raise ValidationError(f"第 {idx + 1} 行不是有效的对象")
            if len(row) > 30:
                raise ValidationError(f"第 {idx + 1} 行字段过多（上限 30）")

        # 提前校验 Agent 名：这里是同步返回 404 的最后机会，
        # 等到后台任务里才发现，用户只能看到一个"已提交但全失败"的任务
        self.agent_service.output_schema(agent)

        now = time.time()
        job = BatchJob(
            job_id=uuid.uuid4().hex[:12],
            agent=agent,
            created_at=now,
            updated_at=now,
            rows=[RowResult(index=i, input=r) for i, r in enumerate(rows)],
            idempotency_key=idempotency_key,
        )
        self._jobs[job.job_id] = job
        if idempotency_key:
            self._idempotency[idempotency_key] = job.job_id
        self._gc()

        task = asyncio.create_task(self._execute(job))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

        logger.info(
            "batch.created",
            extra={"job_id": job.job_id, "agent": agent, "rows": job.total},
        )
        return job

    def get_job(self, job_id: str) -> BatchJob:
        try:
            return self._jobs[job_id]
        except KeyError:
            raise NotFoundError(f"任务不存在或已过期：{job_id}") from None

    def export_rows(self, job: BatchJob) -> list[dict[str, Any]]:
        """把任务结果拍平成表格行：原始输入 + AI 输出 + 校验结论。

        输入列保持运营自己的表头（他们认得自己的表），AI 输出统一加 `out.` 前缀，
        这样回写的表格既能直接看，也能用 Excel 公式按前缀筛选。
        """
        rows: list[dict[str, Any]] = []
        for r in job.rows:
            row: dict[str, Any] = {
                "行号": r.index + 1,
                "处理状态": _ROW_LABELS.get(r.status, r.status),
            }
            row.update({k: str(v) for k, v in r.input.items() if v not in (None, "")})

            if r.data:
                row.update(flatten(r.data, prefix="out."))
            if r.validation:
                row["校验结果"] = _VALIDATION_LABELS.get(
                    r.validation.get("status", ""), r.validation.get("status", "")
                )
                issues = r.validation.get("issues") or []
                if issues:
                    row["校验提示"] = "；".join(str(i.get("message", "")) for i in issues[:5])
            if r.sources:
                row["知识来源"] = "、".join(r.sources[:5])
            if r.error:
                row["错误信息"] = r.error
            rows.append(row)
        return rows

    # --- 内部实现 ------------------------------------------------------
    async def _execute(self, job: BatchJob) -> None:
        """并发执行所有行。单行异常不会中断整批。"""
        job.status = STATUS_RUNNING
        job.updated_at = time.time()

        try:
            schema = self.agent_service.output_schema(job.agent)
        except AppError as exc:
            # 理论上 create_job 已校验过，这里兜住"Agent 在运行期被摘掉"的情况
            job.status = STATUS_FAILED
            job.error = f"{exc.code}: {exc.message}"
            return

        sem = asyncio.Semaphore(self.concurrency)

        async def run_one(row: RowResult) -> None:
            async with sem:
                started = time.perf_counter()
                try:
                    result = await self.agent_service.run(job.agent, row.input)
                    data = result.get("data") or {}
                    row.data = data
                    # 规则校验是批量的生命线：没有它，脏输出会安静地躺进表格
                    row.validation = validate_output(data, schema).to_dict()
                    row.sources = [
                        str(k.get("source", ""))
                        for k in (result.get("knowledge") or [])
                        if k.get("source")
                    ]
                    row.status = ROW_OK
                except AppError as exc:
                    # 类型化错误：带 code，运营能据此判断"该重试还是该改输入"
                    row.status = ROW_FAILED
                    row.error = f"{exc.code}: {exc.message}"
                except Exception as exc:  # 兜底：任何未预期异常都只影响这一行
                    logger.exception(
                        "batch.row_crashed",
                        extra={"job_id": job.job_id, "row": row.index},
                    )
                    row.status = ROW_FAILED
                    row.error = f"未预期错误：{str(exc)[:200]}"
                finally:
                    row.elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
                    job.updated_at = time.time()

        await asyncio.gather(*(run_one(r) for r in job.rows))

        if job.failed_count == 0:
            job.status = STATUS_DONE
        elif job.ok_count == 0:
            job.status = STATUS_FAILED
        else:
            job.status = STATUS_PARTIAL  # 部分成功，失败行留在结果里等人工处理
        job.updated_at = time.time()

        logger.info(
            "batch.finished",
            extra={
                "job_id": job.job_id,
                "status": job.status,
                "ok": job.ok_count,
                "failed": job.failed_count,
                "warned": job.warned_count,
            },
        )
        self._persist(job)

    def _persist(self, job: BatchJob) -> None:
        """结果落盘，供事后追溯与导出（落盘失败不影响主流程）。"""
        try:
            path = os.path.join(self.job_dir, f"{job.job_id}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(job.to_dict(), f, ensure_ascii=False, indent=2)
        except OSError as exc:
            logger.warning("batch.persist_failed", extra={"job_id": job.job_id, "error": str(exc)})

    def _restore(self) -> None:
        """重启后恢复历史任务。

        运行中（running）的任务无法续跑 —— 进程都没了，后台 task 自然也没了。
        明确标记为失败而不是假装还在跑，避免前端一直轮询一个死任务。
        """
        if not os.path.isdir(self.job_dir):
            return
        for name in os.listdir(self.job_dir):
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(self.job_dir, name), encoding="utf-8") as f:
                    payload = json.load(f)
                job = BatchJob(
                    job_id=payload["job_id"],
                    agent=payload["agent"],
                    created_at=payload.get("created_at", 0),
                    updated_at=payload.get("updated_at", 0),
                    status=payload.get("status", STATUS_DONE),
                    idempotency_key=payload.get("idempotency_key"),
                    error=payload.get("error"),
                    rows=[RowResult(**r) for r in payload.get("rows", [])],
                )
                if job.status in (STATUS_PENDING, STATUS_RUNNING):
                    job.status = STATUS_FAILED
                    job.error = "服务重启导致任务中断，请重新提交"
                self._jobs[job.job_id] = job
                if job.idempotency_key:
                    self._idempotency[job.idempotency_key] = job.job_id
            except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
                # 单个损坏文件不该拖垮启动：跳过并留痕即可
                logger.warning("batch.restore_skipped", extra={"file": name, "error": str(exc)})

    def _gc(self) -> None:
        """清理超过 TTL 的任务（惰性，创建任务时顺手做）。"""
        now = time.time()
        expired = [
            jid for jid, job in self._jobs.items() if now - job.updated_at > JOB_TTL_SECONDS
        ]
        for jid in expired:
            job = self._jobs.pop(jid, None)
            if job and job.idempotency_key:
                self._idempotency.pop(job.idempotency_key, None)
            try:
                os.remove(os.path.join(self.job_dir, f"{jid}.json"))
            except OSError:
                pass
        if expired:
            logger.info("batch.gc", extra={"removed": len(expired)})
