import type {
  AgentMeta,
  AgentResult,
  BatchJob,
  BatchJobInit,
  HealthResponse,
  SSEEvent,
} from './types'

const BASE = '/api'

export async function fetchHealth(): Promise<HealthResponse> {
  const r = await fetch(`${BASE}/health`)
  if (!r.ok) throw new Error(`健康检查失败：${r.status}`)
  return r.json()
}

export async function fetchAgents(): Promise<AgentMeta[]> {
  const r = await fetch(`${BASE}/agents`)
  if (!r.ok) throw new Error(`获取 Agent 列表失败：${r.status}`)
  const data = await r.json()
  return data.items as AgentMeta[]
}

/** 非流式执行（不演示打字机，用于对照 / 调试） */
export async function runAgent(agent: string, payload: Record<string, unknown>): Promise<AgentResult> {
  const r = await fetch(`${BASE}/agent/${agent}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ payload }),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({}))
    throw new Error(err?.error?.message || `执行失败：${r.status}`)
  }
  return r.json()
}

// ---------------------------------------------------------------- 批量任务

/** 从后端错误响应里取出可读文案（后端统一返回 {error:{code,message}}） */
async function readError(r: Response, fallback: string): Promise<string> {
  const err = await r.json().catch(() => ({}))
  return err?.error?.message || `${fallback}：${r.status}`
}

export async function createBatch(
  agent: string,
  rows: Record<string, string>[],
  idempotencyKey?: string,
): Promise<BatchJobInit> {
  const r = await fetch(`${BASE}/batch/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ agent, rows, idempotency_key: idempotencyKey || null }),
  })
  if (!r.ok) throw new Error(await readError(r, '创建批量任务失败'))
  return r.json()
}

export async function uploadBatchCsv(
  agent: string,
  file: File,
  idempotencyKey?: string,
): Promise<BatchJobInit> {
  const form = new FormData()
  form.append('agent', agent)
  form.append('file', file)
  if (idempotencyKey) form.append('idempotency_key', idempotencyKey)
  const r = await fetch(`${BASE}/batch/upload`, { method: 'POST', body: form })
  if (!r.ok) throw new Error(await readError(r, '上传 CSV 失败'))
  return r.json()
}

export async function fetchBatch(jobId: string, includeRows = true): Promise<BatchJob> {
  const r = await fetch(`${BASE}/batch/${jobId}?include_rows=${includeRows}`)
  if (!r.ok) throw new Error(await readError(r, '查询任务失败'))
  return r.json()
}

/** 导出结果的下载地址（带 BOM，Excel 双击可开） */
export function batchExportUrl(jobId: string, format: 'csv' | 'json' = 'csv'): string {
  return `${BASE}/batch/${jobId}/export?format=${format}`
}

/** 流式执行：用原生 EventSource 无法带 body，这里用 fetch + ReadableStream 解析 SSE */
export async function* streamAgent(
  agent: string,
  payload: Record<string, unknown>,
  signal?: AbortSignal,
): AsyncGenerator<SSEEvent> {
  const r = await fetch(`${BASE}/agent/${agent}/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ payload }),
    signal,
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({}))
    yield { type: 'error', code: err?.error?.code || 'http_error', message: err?.error?.message || `执行失败：${r.status}` }
    return
  }
  const reader = r.body!.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    // 统一换行符：服务端若以 \r\n\r\n 分帧，只按 \n\n 切会永远切不出完整帧
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n')
    // SSE 帧以空行分隔
    let idx: number
    while ((idx = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, idx)
      buffer = buffer.slice(idx + 2)
      const line = frame.split('\n').find((l) => l.startsWith('data:'))
      if (!line) continue
      const json = line.slice(5).trim()
      if (!json) continue
      try {
        yield JSON.parse(json) as SSEEvent
      } catch {
        // 忽略无法解析的帧
      }
    }
  }
}
