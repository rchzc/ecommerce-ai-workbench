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

// ---------------------------------------------------------------- 批量任务
export type JobStatus = 'pending' | 'running' | 'done' | 'partial' | 'failed'
export type RowStatus = 'ok' | 'failed' | 'pending'
export type ValidationStatus = 'pass' | 'warn' | 'fail'

export interface ValidationIssue {
  field: string
  rule: string
  level: 'warn' | 'fail'
  message: string
}

export interface BatchRowResult {
  index: number
  status: RowStatus
  input: Record<string, unknown>
  data: Record<string, unknown> | null
  validation: { status: ValidationStatus; issues: ValidationIssue[] } | null
  sources: string[]
  error: string | null
  elapsed_ms: number
}

export interface BatchJob {
  job_id: string
  agent: string
  status: JobStatus
  total: number
  finished: number
  ok: number
  failed: number
  warned: number
  need_review: number
  progress: number
  error: string | null
  created_at: number
  updated_at: number
  rows?: BatchRowResult[]
}

export interface BatchJobInit {
  job_id: string
  agent: string
  status: JobStatus
  total: number
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

// ---------------------------------------------------------------- 销售日报 / 数据看板
export interface ReportMetrics {
  revenue: number
  orders: number
  units: number
  ad_spend: number
  shops: number
  skus: number
}

export interface ShopRow {
  shop: string
  revenue: number
  orders: number
  units: number
  ad_spend: number
}

export interface SkuRow {
  shop: string
  sku: string
  revenue: number
  units: number
  stock: number
  safe_stock: number
}

export interface TrendPoint {
  date: string
  revenue: number
  orders: number
}

export interface ReportAlert {
  level: 'high' | 'mid'
  type: string
  message: string
}

export interface DailyReport {
  date: string
  metrics: ReportMetrics
  by_shop: ShopRow[]
  top_skus: SkuRow[]
  trend: TrendPoint[]
  alerts: ReportAlert[]
  markdown: string
  saved_to: string | null
}

// ---------------------------------------------------------------- 全链路流水线
export interface PipelineStep {
  status: 'ok' | 'failed' | 'skipped'
  date?: string
  appended?: number
  mode?: string
  alerts?: number
  rows_created?: number
  reason?: string
  error?: string
  summary?: string
  model?: string
}

export interface PipelineRunResult {
  date: string
  status: 'ok' | 'failed'
  steps: Record<string, PipelineStep>
  report_date?: string
  elapsed_ms?: number
  error?: string
  skipped?: boolean
  reason?: string
}

export interface PipelineStatus {
  enabled: boolean
  schedule: string
  refresh_data: boolean
  use_llm: boolean
  last_run: PipelineRunResult | null
}
