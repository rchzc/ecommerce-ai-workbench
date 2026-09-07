import { useEffect, useRef, useState } from 'react'
import type { AgentMeta, BatchJob } from '../types'
import { batchExportUrl, createBatch, fetchBatch, uploadBatchCsv } from '../api'
import { parseCsv, type ParsedTable } from '../csv'
import { getAgentMeta } from '../forms'
import { Icon } from './Icons'

/** 示例表格：表头即传给 Agent 的字段名，与 listing Agent 的输入一致 */
const SAMPLE_CSV = `product,category,platform,lang
便携折叠防晒伞,户外运动,amazon,en
大容量不锈钢保温杯,家居厨房,amazon,en`

const TERMINAL: BatchJob['status'][] = ['done', 'partial', 'failed']

export function BatchPanel({ agents }: { agents: AgentMeta[] }) {
  const [agent, setAgent] = useState(agents[0]?.name ?? 'listing')
  const [text, setText] = useState('')
  const [table, setTable] = useState<ParsedTable>({ header: [], rows: [] })
  const [file, setFile] = useState<File | null>(null)
  const [job, setJob] = useState<BatchJob | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)

  const meta = getAgentMeta(agent)
  const rowCount = file ? 0 : table.rows.length

  // 粘贴内容变化时即时解析，让用户在提交前就看到"会被当成几行"
  useEffect(() => {
    if (file) return
    setTable(parseCsv(text))
  }, [text, file])

  // 轮询任务进度：终态后停止
  useEffect(() => {
    if (!job || TERMINAL.includes(job.status)) return
    const timer = setInterval(async () => {
      try {
        const latest = await fetchBatch(job.job_id)
        setJob(latest)
      } catch (e) {
        setError((e as Error).message)
        clearInterval(timer)
      }
    }, 1200)
    return () => clearInterval(timer)
  }, [job])

  function onPickFile(f: File | null) {
    setFile(f)
    setError('')
    if (f) setText('')
  }

  async function onSubmit() {
    setError('')
    setBusy(true)
    try {
      // 幂等键：网络重传 / 重复点击不会触发第二次模型调用
      const key = `${agent}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
      const init = file
        ? await uploadBatchCsv(agent, file, key)
        : await createBatch(agent, table.rows, key)
      setJob(await fetchBatch(init.job_id))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const canSubmit = !busy && (file !== null || rowCount > 0)

  return (
    <div className="batch">
      <section className="panel">
        <header className="panel-head">
          <h2>
            <Icon name="layers" size={17} />
            批量任务
          </h2>
          <button className="ghost-btn" onClick={() => { setFile(null); setText(SAMPLE_CSV) }}>
            <Icon name="spark" size={14} />
            填入示例
          </button>
        </header>

        <div className="batch-setup">
          <label className="field">
            <span className="fl">目标智能体</span>
            <select value={agent} onChange={(e) => setAgent(e.target.value)}>
              {agents.map((a) => (
                <option key={a.name} value={a.name}>
                  {a.name} — {a.description || a.domain}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="batch-io">
          <div className="io-col">
            <span className="fl">
              方式一：粘贴表格
              <em>第一行为表头</em>
            </span>
            <textarea
              rows={7}
              className="csv-input"
              value={text}
              placeholder={'product,category\n商品A,类目A\n商品B,类目B'}
              onChange={(e) => { setText(e.target.value); setFile(null) }}
            />
          </div>
          <div className="io-col">
            <span className="fl">
              方式二：上传 CSV
              <em>Excel 另存为 CSV 即可</em>
            </span>
            <div className="uploader" onClick={() => fileRef.current?.click()}>
              <Icon name="upload" size={20} />
              <p>{file ? file.name : '点击选择文件'}</p>
              <span className="muted sm">
                {file ? `${(file.size / 1024).toFixed(1)} KB` : '支持 UTF-8 / GBK 编码，上限 5 MB'}
              </span>
            </div>
            <input
              ref={fileRef}
              type="file"
              accept=".csv,.txt"
              hidden
              onChange={(e) => onPickFile(e.target.files?.[0] ?? null)}
            />
          </div>
        </div>

        {!file && table.rows.length > 0 && (
          <div className="preview">
            <p className="fl">
              解析结果：<strong>{table.rows.length}</strong> 行 · 表头 {table.header.join(' / ')}
            </p>
            <div className="table-wrap">
              <table className="mini-table">
                <thead>
                  <tr>{table.header.map((h) => <th key={h}>{h}</th>)}</tr>
                </thead>
                <tbody>
                  {table.rows.slice(0, 3).map((r, i) => (
                    <tr key={i}>
                      {table.header.map((h) => <td key={h}>{r[h]}</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {error && (
          <div className="err-box">
            <Icon name="alert" size={16} />
            <span>{error}</span>
          </div>
        )}

        <footer className="panel-foot">
          <button
            className="run"
            style={{ '--ac': meta.color } as React.CSSProperties}
            disabled={!canSubmit}
            onClick={onSubmit}
          >
            <Icon name="bolt" size={16} />
            {busy ? '提交中…' : `提交批量任务${file ? '' : `（${rowCount} 行）`}`}
          </button>
          <span className="foot-note">
            逐行调用 {meta.label} → 规则校验 → 结果可回写表格
          </span>
        </footer>
      </section>

      {job && <JobResult job={job} />}
    </div>
  )
}

function JobResult({ job }: { job: BatchJob }) {
  const finished = TERMINAL.includes(job.status)
  const pct = Math.round(job.progress * 100)

  return (
    <section className="panel">
      <header className="panel-head">
        <h2>
          <Icon name="check" size={17} />
          任务 {job.job_id}
        </h2>
        {finished && (
          <a className="ghost-btn" href={batchExportUrl(job.job_id)} download>
            <Icon name="download" size={14} />
            下载结果 CSV
          </a>
        )}
      </header>

      <div className="prog">
        <div className="prog-bar">
          <i style={{ width: `${pct}%` }} />
        </div>
        <span className="prog-txt">
          {job.finished}/{job.total} 行 · {pct}%
        </span>
      </div>

      <div className="stat-row">
        <span className="stat ok">成功 {job.ok}</span>
        <span className="stat warn">需留意 {job.warned}</span>
        <span className="stat review">待人工 {job.need_review}</span>
        <span className="stat fail">失败 {job.failed}</span>
      </div>

      {job.error && <p className="err-line">{job.error}</p>}

      {job.rows && job.rows.length > 0 && (
        <div className="table-wrap tall">
          <table className="mini-table">
            <thead>
              <tr>
                <th>#</th>
                <th>输入</th>
                <th>状态</th>
                <th>校验</th>
                <th>输出摘要</th>
              </tr>
            </thead>
            <tbody>
              {job.rows.map((r) => (
                <tr key={r.index}>
                  <td>{r.index + 1}</td>
                  <td className="cell-input">
                    {Object.entries(r.input)
                      .slice(0, 2)
                      .map(([k, v]) => `${k}: ${v}`)
                      .join(' · ') || '-'}
                  </td>
                  <td>
                    <span className={`tag ${r.status}`}>
                      {r.status === 'ok' ? '成功' : r.status === 'failed' ? '失败' : '处理中'}
                    </span>
                  </td>
                  <td>
                    {r.validation ? (
                      <span className={`tag v-${r.validation.status}`}>
                        {r.validation.status === 'pass'
                          ? '通过'
                          : r.validation.status === 'warn'
                            ? '需留意'
                            : '待人工'}
                      </span>
                    ) : (
                      '-'
                    )}
                  </td>
                  <td className="cell-out">
                    {r.error ? <span className="err-line sm">{r.error}</span> : summarize(r.data)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

/** 输出摘要：取第一个非空字符串字段，避免把整个 JSON 塞进表格 */
function summarize(data: Record<string, unknown> | null): string {
  if (!data) return '-'
  for (const value of Object.values(data)) {
    if (typeof value === 'string' && value.trim()) return value.slice(0, 60)
    if (Array.isArray(value) && value.length && typeof value[0] === 'string') {
      return value[0].slice(0, 60)
    }
  }
  return Object.keys(data).join(' / ')
}
