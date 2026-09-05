import type { AgentMeta, AgentResult, HealthResponse, SSEEvent } from './types'

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
