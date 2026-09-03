// 与后端 /api 接口对齐的类型定义

export interface AgentMeta {
  name: string
  domain: string
  description: string
}

export interface HealthResponse {
  status: string
  provider: string
  provider_label: string
  model_light: string
  model_heavy: string
  embedding_mode: string
  knowledge_chunks: number
  agents: string[]
}

// SSE 事件（与 backend/app/agents/base.py 的 stream() 对齐）
export type SSEEvent =
  | { type: 'meta'; model: string; tier: string; route_score: number; hits: number; retrieve_ms: number }
  | { type: 'knowledge'; items: RetrievedItem[] }
  | { type: 'delta'; text: string }
  | { type: 'done'; elapsed_ms: number; generate_ms: number }
  | { type: 'error'; code: string; message: string }

export interface RetrievedItem {
  text: string
  source: string
  domain: string
  score: number
}

// 非流式结果
export interface AgentResult {
  agent: string
  domain: string
  data: Record<string, unknown>
  knowledge: RetrievedItem[]
  meta: {
    model?: string
    elapsed_ms?: number
    knowledge_hits?: number
    [k: string]: unknown
  }
}

export interface FormField {
  key: string
  label: string
  type: 'text' | 'textarea' | 'number' | 'select'
  placeholder?: string
  options?: { value: string; label: string }[]
  example?: string
  hint?: string
}
