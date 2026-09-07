"""批量任务与对外 Webhook 控制器。

严格约束：与 routes.py 一样，这一层不写业务逻辑。
只做：解析请求（含 CSV 文件） → 调用服务 → 格式化响应。

两类端点，对应两类使用者：
1. /api/batch/*  —— 给内部前端用，同源访问，与既有 /api/agent 一致的鉴权策略（不鉴权）
2. /api/hooks/*  —— 给 n8n / Dify / Coze 等外部编排工具调用，必须带 X-API-Key

为什么分开放：对外开放意味着任何人拿到地址都能刷模型额度，
分开后可以在网关层单独给 /api/hooks 加限流，而不影响内部调用。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile

from ..config import Settings
from ..core.auth import verify_api_key
from ..core.tabular import parse_csv, to_csv
from ..core.validators import validate_output
from ..errors import ValidationError
from ..schemas import BatchRunRequest, BatchRunResponse, HookRequest
from ..services.agent_service import AgentService
from ..services.batch_service import BatchService
from . import deps

logger = logging.getLogger(__name__)

router = APIRouter()

# 上传文件的大小上限：超过这个体积，说明传错文件了，没必要往下走
MAX_UPLOAD_BYTES = 5 * 1024 * 1024


@router.post("/batch/run", response_model=BatchRunResponse, summary="创建批量任务")
async def run_batch(
    body: BatchRunRequest,
    service: BatchService = Depends(deps.get_batch_service),
) -> BatchRunResponse:
    """提交一批数据，立即返回 job_id，任务在后台执行。

    为什么不阻塞等待：200 行要跑几分钟，HTTP 早就超时了。
    立即返回 + 前端轮询才是长任务的正确姿势。
    """
    job = service.create_job(body.agent, body.rows, idempotency_key=body.idempotency_key)
    return BatchRunResponse(
        job_id=job.job_id, agent=job.agent, status=job.status, total=job.total
    )


@router.post("/batch/upload", response_model=BatchRunResponse, summary="上传 CSV 创建批量任务")
async def upload_batch(
    agent: str = Form(..., description="目标 Agent 名称"),
    file: UploadFile = File(..., description="CSV 文件，第一行为表头"),
    idempotency_key: str | None = Form(None),
    service: BatchService = Depends(deps.get_batch_service),
) -> BatchRunResponse:
    """运营最自然的入口：直接把 Excel 导出的 CSV 传上来。

    Excel 另存的 CSV 在中文 Windows 下是 GBK，所以这里做编码兜底：
    先按 UTF-8（带 BOM）解，失败再退 GBK。解不开就明确报 422，
    而不是让表格变成一堆乱码还"成功"执行。
    """
    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValidationError(
            f"文件过大（{len(raw) // 1024} KB），单次上传上限 {MAX_UPLOAD_BYTES // 1024 // 1024} MB"
        )

    text = _decode(raw)
    rows = parse_csv(text)
    if not rows:
        raise ValidationError("未能从文件中解析出数据行（至少需要表头 + 1 行数据）")

    job = service.create_job(agent, rows, idempotency_key=idempotency_key)
    logger.info(
        "batch.uploaded",
        extra={"job_id": job.job_id, "agent": agent, "rows": job.total, "file": file.filename},
    )
    return BatchRunResponse(
        job_id=job.job_id, agent=job.agent, status=job.status, total=job.total
    )


@router.get("/batch/{job_id}", summary="查询任务进度与结果")
async def get_batch(
    job_id: str,
    include_rows: bool = True,
    service: BatchService = Depends(deps.get_batch_service),
) -> dict:
    """轮询端点。任务量大时传 include_rows=false 只取进度，省带宽。"""
    job = service.get_job(job_id)
    return job.to_dict(include_rows=include_rows)


@router.get("/batch/{job_id}/export", summary="导出任务结果（CSV / JSON）")
async def export_batch(
    job_id: str,
    format: str = "csv",
    service: BatchService = Depends(deps.get_batch_service),
) -> Response:
    """把结果回写成表格 —— 这是整条链路的终点，也是业务真正拿走的东西。

    任务未跑完也允许导出：运营常常只想先看看前几行靠不靠谱，
    不必等整批结束。
    """
    job = service.get_job(job_id)
    rows = service.export_rows(job)

    if format.lower() == "json":
        from fastapi.responses import JSONResponse

        return JSONResponse(content={"job_id": job.job_id, "agent": job.agent, "rows": rows})

    if format.lower() != "csv":
        raise ValidationError(f"不支持的导出格式：{format}（可选 csv / json）")

    csv_text = to_csv(rows)
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{job.agent}_{job.job_id}.csv"'
        },
    )


@router.post("/hooks/agent/{name}", summary="Webhook：供 n8n / Dify / Coze 调用")
async def hook_agent(
    name: str,
    body: HookRequest,
    request: Request,
    service: AgentService = Depends(deps.get_agent_service),
    settings: Settings = Depends(deps.get_settings),
) -> dict:
    """对外单点调用接口。

    与内部 /api/agent/{name} 的区别：
    1. 需要 X-API-Key；
    2. **返回值多一个 validation 字段** —— 外部系统拿到结果就能判断
        "这条能不能直接用、要不要转人工"，不必自己再写一遍校验规则。
       这是让 AI 能力嵌进自动化流程的关键：流程需要可判定的信号，而不只是一段文本。
    """
    # request 由 FastAPI 注入，用于读取 X-API-Key 与来源 IP（日志留痕）
    verify_api_key(request.headers.get("X-API-Key"), settings.workflow_api_key)

    result = await service.run(name, body.payload)
    schema = service.output_schema(name)
    validation = validate_output(result.get("data") or {}, schema)

    logger.info(
        "hook.called",
        extra={"agent": name, "status": validation.status, "peer": request.client.host if request.client else "-"},
    )
    return {**result, "validation": validation.to_dict()}


def _decode(raw: bytes) -> str:
    """CSV 解码：UTF-8(BOM) 优先，退回 GBK，再退回 GB18030。

    中文 Windows 的 Excel 默认导 GBK，香港/台湾的同事还会给来 Big5，
    逐级兜底比"解不出来报 500"友好得多。
    """
    for enc in ("utf-8-sig", "gbk", "gb18030", "big5"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    # 最后兜底：替换非法字符，保证流程能继续而不是直接 500
    return raw.decode("utf-8", errors="replace")
