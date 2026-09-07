import { useEffect, useRef, useState } from 'react'
import type { AgentMeta, HealthResponse, RetrievedItem } from './types'
import { fetchAgents, fetchHealth, runAgent, streamAgent } from './api'
import {
  AGENT_EXAMPLES,
  AGENT_META,
  GENERIC_FIELD,
  defaultPayload,
  getAgentForms,
  getAgentMeta,
} from './forms'
import { AgentResultView } from './components/Results'
import { BatchPanel } from './components/BatchPanel'
import { Icon } from './components/Icons'
import './styles.css'

type RunState = 'idle' | 'running' | 'done' | 'error'

interface Telemetry {
  model?: string
  tier?: string
  hits?: number
  retrieveMs?: number
  elapsedMs?: number
}

/** 后端拉不到列表时的兜底：用前端内置配置顶上，保证离线开发也能用 */
function localAgentList(): AgentMeta[] {
  return Object.keys(AGENT_META).map((name) => ({
    name,
    domain: name,
    description: AGENT_META[name].tagline,
  }))
}

export default function App() {
  const [tab, setTab] = useState<'single' | 'batch'>('single')
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [agents, setAgents] = useState<AgentMeta[]>([])
  const [active, setActive] = useState('selection')
  const [payload, setPayload] = useState<Record<string, string>>(defaultPayload('selection'))
  const [state, setState] = useState<RunState>('idle')
  const [error, setError] = useState('')
  const [rawText, setRawText] = useState('')
  const [streamText, setStreamText] = useState('')
  const [data, setData] = useState<Record<string, unknown> | null>(null)
  const [tele, setTele] = useState<Telemetry>({})
  const [sources, setSources] = useState<RetrievedItem[]>([])
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    fetchHealth().then(setHealth).catch(() => {})
    // 以 /api/agents 为准：后端新增 Agent 后前端自动出现，无需改代码
    fetchAgents().then(setAgents).catch(() => setAgents([]))
  }, [])

  const agentList = agents.length ? agents : localAgentList()
  // 后端列表里没有当前选中项时（例如注册表改过）自动落到第一项，
  // 用派生值而不是 useEffect，避免多一次渲染循环
  const activeName = agentList.some((a) => a.name === active)
    ? active
    : agentList[0]?.name ?? active

  const meta = getAgentMeta(activeName)
  const fields = getAgentForms(activeName)
  // 未预置表单的 Agent 走通用输入框
  const formFields = fields.length ? fields : [GENERIC_FIELD]

  function selectAgent(name: string) {
    setActive(name)
    setPayload(defaultPayload(name))
    resetRun()
  }

  function resetRun() {
    setState('idle')
    setError('')
    setRawText('')
    setStreamText('')
    setData(null)
    setTele({})
    setSources([])
  }

  function fillExample() {
    setPayload({ ...defaultPayload(activeName), ...(AGENT_EXAMPLES[activeName] || {}) })
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
      for await (const ev of streamAgent(activeName, clean, ctrl.signal)) {
        if (ev.type === 'meta') {
          setTele({ model: ev.model, tier: ev.tier, hits: ev.hits, retrieveMs: ev.retrieve_ms })
        } else if (ev.type === 'knowledge') {
          setSources(ev.items)
        } else if (ev.type === 'delta') {
          text += ev.text
          setStreamText(text)
        } else if (ev.type === 'done') {
          setTele((t) => ({ ...t, elapsedMs: ev.elapsed_ms }))
        } else if (ev.type === 'error') {
          fail(ev.message || '执行出错', text)
          return
        }
      }

      // 三级容错也解析不出 JSON 时必须明确报错。
      // 之前这里照常 setState('done')，结果页面停在打字机动画上，
      // 用户只看到一堆原始文本和永远转不完的光标，不知道到底成功还是失败。
      const parsed = parseLenient(text)
      if (!parsed) {
        fail(
          text.trim()
            ? '模型输出无法解析为 JSON（已尝试三级容错），请重试或换个模型'
            : '模型未返回任何内容',
          text,
        )
        return
      }
      setData(parsed)
      setState('done')
    } catch (e) {
      if ((e as Error).name === 'AbortError') return
      // 流式通道本身出问题（网络中断 / 代理缓冲 / 浏览器限制）时，
      // 自动退回非流式接口再试一次 —— 有结果总比直接报错好
      try {
        const result = await runAgent(activeName, clean)
        setData(result.data)
        setSources(result.knowledge || [])
        setTele({
          model: String(result.meta?.model ?? ''),
          hits: result.meta?.knowledge_hits,
          elapsedMs: result.meta?.elapsed_ms,
        })
        setState('done')
      } catch (e2) {
        fail(`${(e as Error).message}（已尝试降级为非流式调用，仍失败：${(e2 as Error).message}）`)
      }
    }
  }

  /** 统一走这里设置错误态，顺便把原始文本留下供用户核对 */
  function fail(message: string, raw: string = '') {
    setError(message)
    setRawText(raw)
    setState('error')
  }

  return (
    <div className="app">
      <Header health={health} />

      <div className="tabbar">
        <button className={`tab ${tab === 'single' ? 'on' : ''}`} onClick={() => setTab('single')}>
          <Icon name="spark" size={15} />
          单条分析
        </button>
        <button className={`tab ${tab === 'batch' ? 'on' : ''}`} onClick={() => setTab('batch')}>
          <Icon name="layers" size={15} />
          批量任务
        </button>
      </div>

      {tab === 'batch' ? (
        <div className="layout">
          <main className="col-main batch-col">
            <BatchPanel agents={agentList} />
          </main>
        </div>
      ) : (
      <div className="layout">
        {/* 左：智能体卡片 */}
        <aside className="col-left">
          <p className="col-title">运营智能体</p>
          {agentList.map((a) => {
            const m = getAgentMeta(a.name)
            return (
              <button
                key={a.name}
                className={`agent-card ${a.name === activeName ? 'on' : ''}`}
                style={{ '--ac': m.color } as React.CSSProperties}
                onClick={() => selectAgent(a.name)}
              >
                <span className="ag-icon">
                  <Icon name={m.icon} size={19} />
                </span>
                <span className="ag-text">
                  <strong>{m.label}</strong>
                  <em>{m.tagline}</em>
                </span>
              </button>
            )
          })}
        </aside>

        {/* 中：输入 + 结果 */}
        <main className="col-main">
          <section className="panel">
            <header className="panel-head">
              <h2>
                <Icon name={meta.icon} size={17} />
                {meta.label}
              </h2>
              <button className="ghost-btn" onClick={fillExample}>
                <Icon name="spark" size={14} />
                填入示例
              </button>
            </header>

            <div className="form-grid">
              {formFields.map((f) => (
                <label key={f.key} className={`field ${f.type === 'textarea' ? 'full' : ''}`}>
                  <span className="fl">
                    {f.label}
                    {f.hint && <em>{f.hint}</em>}
                  </span>
                  {f.type === 'textarea' ? (
                    <textarea
                      rows={3}
                      value={payload[f.key] || ''}
                      placeholder={f.placeholder}
                      onChange={(e) => setPayload((p) => ({ ...p, [f.key]: e.target.value }))}
                    />
                  ) : f.type === 'select' ? (
                    <select
                      value={payload[f.key] || ''}
                      onChange={(e) => setPayload((p) => ({ ...p, [f.key]: e.target.value }))}
                    >
                      {(f.options || []).map((o) => (
                        <option key={o.value} value={o.value}>
                          {o.label}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <input
                      type={f.type === 'number' ? 'number' : 'text'}
                      step="any"
                      value={payload[f.key] || ''}
                      placeholder={f.placeholder}
                      onChange={(e) => setPayload((p) => ({ ...p, [f.key]: e.target.value }))}
                    />
                  )}
                </label>
              ))}
            </div>

            <footer className="panel-foot">
              {state === 'running' ? (
                <button className="run stop" onClick={() => abortRef.current?.abort()}>
                  停止
                </button>
              ) : (
                <button className="run" onClick={onRun} style={{ '--ac': meta.color } as React.CSSProperties}>
                  <Icon name="bolt" size={16} />
                  运行分析
                </button>
              )}
              <span className="foot-note">先检索知识库 → 自动选模型 → 流式输出</span>
            </footer>
          </section>

          <section className="panel result">
            <ResultBody
              state={state}
              error={error}
              rawText={rawText}
              streamText={streamText}
              data={data}
              agent={activeName}
              tele={tele}
              chunks={health?.knowledge_chunks ?? 0}
            />
          </section>
        </main>

        {/* 右：遥测 + 知识 */}
        <aside className="col-right">
          <p className="col-title">本次调用</p>
          <div className="panel tele">
            {tele.model ? (
              <>
                <div className="tele-row">
                  <span>路由模型</span>
                  <strong className={tele.tier === 'heavy' ? 'heavy' : 'light'}>
                    {tele.model}
                    <em>{tele.tier === 'heavy' ? '重量' : '轻量'}</em>
                  </strong>
                </div>
                <div className="tele-row">
                  <span>知识命中</span>
                  <strong>{tele.hits ?? 0} 条</strong>
                </div>
                {tele.retrieveMs != null && (
                  <div className="tele-row">
                    <span>检索耗时</span>
                    <strong>{Math.round(tele.retrieveMs)} ms</strong>
                  </div>
                )}
                {tele.elapsedMs != null && (
                  <div className="tele-row">
                    <span>总耗时</span>
                    <strong>{(tele.elapsedMs / 1000).toFixed(1)} s</strong>
                  </div>
                )}
              </>
            ) : (
              <p className="muted sm">运行后显示路由与耗时</p>
            )}
          </div>

          <p className="col-title">
            命中的知识
            {sources.length > 0 && <span className="cnt">{sources.length}</span>}
          </p>
          <div className="src-list">
            {sources.length === 0 ? (
              <p className="muted sm">{state === 'running' ? '检索中…' : '运行后显示命中的知识片段'}</p>
            ) : (
              sources.map((s, i) => (
                <div className="src" key={i}>
                  <div className="src-top">
                    <span className="chip dom">{s.domain}</span>
                    <span className="score">{Math.round(Number(s.score) * 100)}%</span>
                  </div>
                  <p className="src-file">{s.source}</p>
                  <p className="src-text">{s.text}</p>
                </div>
              ))
            )}
          </div>
        </aside>
      </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------- 子组件

function Header({ health }: { health: HealthResponse | null }) {
  return (
    <header className="topbar">
      <div className="brand">
        <span className="logo">
          <Icon name="spark" size={22} />
        </span>
        <div>
          <h1>跨境电商 AI 运营工作台</h1>
          <p>RAG 知识库 + 6 个运营智能体 · 真实模型调用</p>
        </div>
      </div>
      <div className="status">
        {health ? (
          <>
            <span className="pill ok">
              <i className="dot-live" />
              {health.provider_label}
            </span>
            <span className="pill">
              {health.model_light} / {health.model_heavy}
            </span>
            <span className="pill strong">{health.knowledge_chunks} 切片</span>
          </>
        ) : (
          <span className="pill off">后端未连接</span>
        )}
      </div>
    </header>
  )
}

function ResultBody({
  state,
  error,
  rawText,
  streamText,
  data,
  agent,
  tele,
  chunks,
}: {
  state: RunState
  error: string
  rawText: string
  streamText: string
  data: Record<string, unknown> | null
  agent: string
  tele: Telemetry
  chunks: number
}) {
  if (state === 'error')
    return (
      <div className="err-box">
        <Icon name="alert" size={16} />
        <div>
          <span>{error}</span>
          {rawText && <pre className="stream-text raw-out">{rawText}</pre>}
        </div>
      </div>
    )

  if (state === 'idle')
    return (
      <div className="empty">
        <span className="empty-ico">
          <Icon name="bolt" size={26} />
        </span>
        <h3>选一个智能体，点「填入示例」再运行</h3>
        <p>
          {chunks > 0
            ? `先检索 ${chunks} 条运营知识切片，再交给模型输出结构化结论`
            : '先检索运营知识切片，再交给模型输出结构化结论'}
        </p>
      </div>
    )

  if (data) return <AgentResultView agent={agent} data={data} />

  if (state === 'running' && !streamText)
    return (
      <div className="empty">
        <span className="spinner" />
        <h3>正在检索知识库…</h3>
        <p>命中 {tele.hits ?? 0} 条，等待模型返回</p>
      </div>
    )

  // 流式输出中：打字机效果
  return (
    <div className="streaming">
      <span className="typing">
        <i />
        <i />
        <i />
      </span>
      <pre className="stream-text">{streamText}</pre>
    </div>
  )
}

// 前端轻量 JSON 容错（与后端 parse_json_lenient 同思路）：直接解析 → 去围栏 → 截首对象
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
