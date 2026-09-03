import { useEffect, useRef, useState } from 'react'
import type { AgentMeta, FormField, HealthResponse, RetrievedItem, SSEEvent } from './types'
import { fetchAgents, fetchHealth, streamAgent } from './api'
import { AGENT_FORMS, defaultPayload } from './forms'
import './styles.css'

type RunState = 'idle' | 'running' | 'done' | 'error'

export default function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [agents, setAgents] = useState<AgentMeta[]>([])
  const [active, setActive] = useState<string>('selection')
  const [payload, setPayload] = useState<Record<string, string>>(defaultPayload('selection'))
  const [state, setState] = useState<RunState>('idle')
  const [error, setError] = useState<string>('')
  const [rawText, setRawText] = useState<string>('') // 流式累积的模型文本（含 JSON）
  const [rawJson, setRawJson] = useState<Record<string, unknown> | null>(null)
  const [meta, setMeta] = useState<SSEEvent | null>(null)
  const [sources, setSources] = useState<RetrievedItem[]>([])
  const [timing, setTiming] = useState<{ elapsed_ms?: number; generate_ms?: number }>({})
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    fetchHealth().then(setHealth).catch(() => {})
    fetchAgents().then(setAgents).catch(() => {})
  }, [])

  function selectAgent(name: string) {
    setActive(name)
    setPayload(defaultPayload(name))
    resetRun()
  }

  function resetRun() {
    setState('idle')
    setError('')
    setRawText('')
    setRawJson(null)
    setMeta(null)
    setSources([])
    setTiming({})
  }

  function onField(key: string, value: string) {
    setPayload((p) => ({ ...p, [key]: value }))
  }

  async function onRun() {
    abortRef.current?.abort()
    const ctrl = new AbortController()
    abortRef.current = ctrl
    resetRun()
    setState('running')

    const clean = Object.fromEntries(
      Object.entries(payload).filter(([, v]) => String(v).trim() !== ''),
    )

    let text = ''

    try {
      for await (const ev of streamAgent(active, clean, ctrl.signal)) {
        if (ev.type === 'meta') {
          setMeta(ev)
        } else if (ev.type === 'knowledge') {
          setSources(ev.items)
        } else if (ev.type === 'delta') {
          text += ev.text
          setRawText(text)
        } else if (ev.type === 'done') {
          setTiming({ elapsed_ms: ev.elapsed_ms, generate_ms: ev.generate_ms })
        } else if (ev.type === 'error') {
          setError(ev.message || '执行出错')
          setState('error')
          return
        }
      }
      // 流结束后把累积的文本尝试解析为结构化 JSON
      const parsed = parseLenient(text)
      setRawJson(parsed)
      setState('done')
    } catch (e) {
      if ((e as Error).name === 'AbortError') return
      setError((e as Error).message)
      setState('error')
    }
  }

  function onStop() {
    abortRef.current?.abort()
    setState('idle')
  }

  const fields = AGENT_FORMS[active] || []
  const activeMeta = agents.find((a) => a.name === active)

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo">◈</span>
          <div>
            <h1>跨境电商 AI 运营工作台</h1>
            <p>RAG + 多 Agent · 一人全栈交付 · 现场可跑</p>
          </div>
        </div>
        <HealthBadge health={health} />
      </header>

      <div className="layout">
        {/* 左：Agent 列表 */}
        <aside className="sidebar">
          <div className="sidebar-title">运营智能体</div>
          {agents.length === 0 && <div className="hint">加载中…</div>}
          {agents.map((a) => (
            <button
              key={a.name}
              className={`agent-item ${a.name === active ? 'active' : ''}`}
              onClick={() => selectAgent(a.name)}
            >
              <span className="agent-name">{a.name}</span>
              <span className="agent-desc">{a.description}</span>
            </button>
          ))}
        </aside>

        {/* 中：表单 + 结果 */}
        <main className="main">
          <section className="card form-card">
            <h2>{activeMeta?.description || '输入需求'}</h2>
            <div className="form-grid">
              {fields.map((f) => (
                <FieldInput key={f.key} field={f} value={payload[f.key] || ''} onChange={(v) => onField(f.key, v)} />
              ))}
            </div>
            <div className="actions">
              {state === 'running' ? (
                <button className="btn stop" onClick={onStop}>
                  停止
                </button>
              ) : (
                <button className="btn primary" onClick={onRun}>
                  运行 {active}
                </button>
              )}
              <span className="hint">
                流式调用 · 先检索知识库 → 自动选模型 → 逐字输出
              </span>
            </div>
          </section>

          <section className="card result-card">
            <div className="result-header">
              <h2>输出</h2>
              {meta && <RouteChip meta={meta} />}
            </div>
            <ResultBody
              state={state}
              error={error}
              rawText={rawText}
              rawJson={rawJson}
              timing={timing}
            />
          </section>
        </main>

        {/* 右：知识库 + 路由 */}
        <aside className="sidebar right">
          <div className="sidebar-title">检索到的知识</div>
          {sources.length === 0 ? (
            <div className="hint">{state === 'running' ? '检索中…' : '运行后显示命中片段'}</div>
          ) : (
            sources.map((s, i) => (
              <div className="source" key={i}>
                <div className="source-meta">
                  <span className="source-domain">{s.domain}</span>
                  <span className="source-score">相关度 {Number(s.score).toFixed(3)}</span>
                </div>
                <div className="source-source">{s.source}</div>
                <p className="source-text">{s.text}</p>
              </div>
            ))
          )}
        </aside>
      </div>

      <footer className="footer">
        后端 /api/health · /api/agents · /agent/{'{name}'} · /agent/{'{name}'}/stream · 单容器交付（FastAPI 托管前端）
      </footer>
    </div>
  )
}

// ---------- 子组件 ----------

function HealthBadge({ health }: { health: HealthResponse | null }) {
  if (!health) return <div className="badge off">后端未连接</div>
  return (
    <div className="badge ok" title={`${health.provider_label} · 知识库 ${health.knowledge_chunks} 切片`}>
      <span className="dot" />
      {health.provider_label} · {health.model_light}/{health.model_heavy}
      <span className="badge-sub">· {health.knowledge_chunks} 切片</span>
    </div>
  )
}

function FieldInput({ field, value, onChange }: { field: FormField; value: string; onChange: (v: string) => void }) {
  return (
    <label className={`field ${field.type === 'textarea' ? 'full' : ''}`}>
      <span className="field-label">
        {field.label}
        {field.hint && <em className="field-hint">{field.hint}</em>}
      </span>
      {field.type === 'textarea' ? (
        <textarea
          rows={3}
          value={value}
          placeholder={field.placeholder}
          onChange={(e) => onChange(e.target.value)}
        />
      ) : field.type === 'select' ? (
        <select value={value} onChange={(e) => onChange(e.target.value)}>
          {(field.options || []).map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      ) : (
        <input
          type={field.type === 'number' ? 'number' : 'text'}
          step="any"
          value={value}
          placeholder={field.placeholder}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
      {field.example && (
        <span className="field-example">
          示例：{field.example}
        </span>
      )}
    </label>
  )
}

function RouteChip({ meta }: { meta: SSEEvent }) {
  if (meta.type !== 'meta') return null
  return (
    <span className={`route ${meta.tier}`}>
      {meta.tier === 'heavy' ? '重量模型' : '轻量模型'} · {meta.model}
      <em>路由分 {meta.route_score}</em>
    </span>
  )
}

function ResultBody({
  state,
  error,
  rawText,
  rawJson,
  timing,
}: {
  state: RunState
  error: string
  rawText: string
  rawJson: Record<string, unknown> | null
  timing: { elapsed_ms?: number; generate_ms?: number }
}) {
  if (state === 'error') return <pre className="error-box">{error}</pre>
  if (state === 'idle') return <div className="hint big">选择左上角的智能体，填写需求后点「运行」。</div>
  if (state === 'running' && !rawText)
    return <div className="hint big">正在检索知识库并调用模型…</div>
  if (rawJson) {
    return (
      <div className="result-scroll">
        <JsonView data={rawJson} />
        {timing.elapsed_ms != null && (
          <div className="timing">
            总耗时 {timing.elapsed_ms}ms · 生成 {timing.generate_ms ?? '-'}ms
          </div>
        )}
      </div>
    )
  }
  // 流式过程中还没解析出来：直接显示原始文本（打字机效果）
  return <pre className="stream-text">{rawText}</pre>
}

function JsonView({ data }: { data: unknown }) {
  if (data == null) return <span className="muted">null</span>
  if (typeof data !== 'object') return <span>{String(data)}</span>
  if (Array.isArray(data)) {
    return (
      <ul className="jv-list">
        {data.map((item, i) => (
          <li key={i}>
            <JsonView data={item} />
          </li>
        ))}
      </ul>
    )
  }
  return (
    <div className="jv-obj">
      {Object.entries(data as Record<string, unknown>).map(([k, v]) => (
        <div className="jv-row" key={k}>
          <span className="jv-key">{k}</span>
          <span className="jv-val">
            <JsonView data={v} />
          </span>
        </div>
      ))}
    </div>
  )
}

// 前端自己的轻量 JSON 容错：去围栏 + 截首对象（与后端 parse_json_lenient 思路一致）
function parseLenient(text: string): Record<string, unknown> | null {
  const t = text.trim()
  if (!t) return null
  const tryParse = (s: string): Record<string, unknown> | null => {
    try {
      const o = JSON.parse(s)
      return o && typeof o === 'object' ? (o as Record<string, unknown>) : null
    } catch {
      return null
    }
  }
  let r = tryParse(t)
  if (r) return r
  const fence = t.match(/```(?:json)?\s*([\s\S]*?)\s*```/)
  if (fence) {
    r = tryParse(fence[1])
    if (r) return r
  }
  const start = t.indexOf('{')
  if (start !== -1) {
    let depth = 0
    let inStr = false
    let esc = false
    for (let i = start; i < t.length; i++) {
      const ch = t[i]
      if (inStr) {
        if (esc) esc = false
        else if (ch === '\\') esc = true
        else if (ch === '"') inStr = false
        continue
      }
      if (ch === '"') inStr = true
      else if (ch === '{') depth++
      else if (ch === '}') {
        depth--
        if (depth === 0) {
          r = tryParse(t.slice(start, i + 1))
          if (r) return r
          break
        }
      }
    }
  }
  return null
}
