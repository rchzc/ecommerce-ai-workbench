import { useCallback, useEffect, useState } from 'react'
import type { DailyReport, PipelineRunResult, PipelineStatus } from '../types'
import { fetchDailyReport, fetchPipelineStatus, fetchReportDates, runPipeline, syncDailyReport } from '../api'
import { Icon } from './Icons'

type LoadState = 'loading' | 'ready' | 'error'

const money = (v: number) => v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

/**
 * 数据看板：销售日报 + 规则预警 + 飞书同步。
 *
 * 预警由后端规则引擎产出（不调大模型），这里只做渲染，
 * 不在前端重复实现业务规则 —— 前后端各守各的边界。
 */
export function DashboardPanel() {
  const [state, setState] = useState<LoadState>('loading')
  const [error, setError] = useState('')
  const [report, setReport] = useState<DailyReport | null>(null)
  const [dates, setDates] = useState<string[]>([])
  const [syncing, setSyncing] = useState(false)
  const [syncMsg, setSyncMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [pipeline, setPipeline] = useState<PipelineStatus | null>(null)
  const [pipelineRunning, setPipelineRunning] = useState(false)
  const [pipelineMsg, setPipelineMsg] = useState<PipelineRunResult | null>(null)

  const load = useCallback(async (date?: string) => {
    setState('loading')
    setError('')
    try {
      const r = await fetchDailyReport(date)
      setReport(r)
      setState('ready')
    } catch (e) {
      setError((e as Error).message)
      setState('error')
    }
  }, [])

  useEffect(() => {
    fetchDailyReport()
      .then((r) => {
        setReport(r)
        setState('ready')
      })
      .catch((e) => {
        setError((e as Error).message)
        setState('error')
      })
    fetchReportDates()
      .then((d) => setDates(d.items))
      .catch(() => {})
    fetchPipelineStatus()
      .then(setPipeline)
      .catch(() => {})
  }, [])

  async function onRunPipeline(force: boolean) {
    if (pipelineRunning) return
    setPipelineRunning(true)
    try {
      const result = await runPipeline(force)
      setPipelineMsg(result)
      // 流水线刷新了数据，日报也要重新拉
      await load()
      fetchPipelineStatus().then(setPipeline).catch(() => {})
    } catch (e) {
      setPipelineMsg({
        date: '',
        status: 'failed',
        steps: {},
        error: (e as Error).message,
      })
    } finally {
      setPipelineRunning(false)
    }
  }

  async function onSync() {
    if (!report || syncing) return
    setSyncing(true)
    setSyncMsg(null)
    try {
      const res = await syncDailyReport(report.date)
      setSyncMsg({ ok: true, text: `已写入飞书多维表格（${res.created} 行）` })
    } catch (e) {
      setSyncMsg({ ok: false, text: (e as Error).message })
    } finally {
      setSyncing(false)
    }
  }

  if (state === 'error')
    return (
      <section className="panel">
        <div className="err-box">
          <Icon name="alert" size={16} />
          <div>
            <span>{error}</span>
            <p className="muted sm">
              请确认 backend/data/sales/ 下有符合列约定的销售 CSV（参考 sample_sales.csv）
            </p>
          </div>
        </div>
      </section>
    )

  if (state === 'loading' || !report)
    return (
      <section className="panel">
        <div className="empty">
          <span className="spinner" />
          <h3>正在聚合日报…</h3>
        </div>
      </section>
    )

  const m = report.metrics
  const maxRevenue = Math.max(...report.trend.map((t) => t.revenue), 1)
  const prev = report.trend.length > 1 ? report.trend[report.trend.length - 2].revenue : 0
  const mom = prev > 0 ? (m.revenue - prev) / prev : null

  return (
    <section className="panel">
      <header className="panel-head">
        <h2>
          <Icon name="chart" size={17} />
          销售日报 · {report.date}
        </h2>
        <div className="dash-actions">
          {dates.length > 0 && (
            <select
              className="dash-date"
              value={report.date}
              onChange={(e) => load(e.target.value)}
            >
              {dates.map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))}
            </select>
          )}
          <button className="ghost-btn" onClick={onSync} disabled={syncing}>
            <Icon name="layers" size={14} />
            {syncing ? '同步中…' : '同步到飞书'}
          </button>
        </div>
      </header>

      {syncMsg && (
        <p className={`sync-msg ${syncMsg.ok ? 'ok' : 'bad'}`}>
          {syncMsg.ok ? <Icon name="spark" size={13} /> : <Icon name="alert" size={13} />}
          {syncMsg.text}
        </p>
      )}

      {/* 全链路流水线卡片 */}
      <div className="pipeline-card">
        <div className="pipeline-info">
          <strong>
            <Icon name="refresh" size={14} />
            全链路流水线
          </strong>
          <span className="muted sm">
            {pipeline
              ? pipeline.enabled
                ? `每天 ${pipeline.schedule} 自动执行：店铺拉数 → 日报 + 规则预警 → 飞书多维表格`
                : '自动调度已关闭（PIPELINE_ENABLED=false），可手动执行'
              : '店铺拉数 → 日报 + 规则预警 → 飞书多维表格'}
          </span>
          {pipeline?.last_run && (
            <span className={`pipe-last ${pipeline.last_run.status}`}>
              上次：{pipeline.last_run.date} ·{' '}
              {pipeline.last_run.status === 'ok' ? '成功' : `失败（${pipeline.last_run.error ?? '未知'}）`}
            </span>
          )}
        </div>
        <div className="pipeline-actions">
          <button className="ghost-btn" onClick={() => onRunPipeline(false)} disabled={pipelineRunning}>
            <Icon name="bolt" size={14} />
            {pipelineRunning ? '执行中…' : '执行流水线'}
          </button>
          <button className="ghost-btn" onClick={() => onRunPipeline(true)} disabled={pipelineRunning}>
            强制重跑
          </button>
        </div>
      </div>
      {pipelineMsg && (
        <p className={`sync-msg ${pipelineMsg.status === 'ok' ? 'ok' : 'bad'}`}>
          {pipelineMsg.status === 'ok'
            ? `链路执行成功：${pipelineMsg.steps?.refresh?.status === 'ok' ? `新增 ${pipelineMsg.steps.refresh.appended} 行数据` : pipelineMsg.steps?.refresh?.reason ?? '数据无变化'}，日报 ${pipelineMsg.report_date ?? ''}，写入飞书 ${pipelineMsg.steps?.feishu?.rows_created ?? 0} 行`
            : `链路失败：${pipelineMsg.error ?? pipelineMsg.steps?.feishu?.error ?? '未知错误'}`}
        </p>
      )}

      {/* KPI 卡 */}
      <div className="kpi-grid">
        <div className="kpi">
          <span className="kpi-label">GMV</span>
          <strong className="kpi-value">¥{money(m.revenue)}</strong>
          {mom !== null && (
            <span className={`kpi-delta ${mom >= 0 ? 'up' : 'down'}`}>
              环比 {(mom >= 0 ? '+' : '') + (mom * 100).toFixed(1)}%
            </span>
          )}
        </div>
        <div className="kpi">
          <span className="kpi-label">订单数</span>
          <strong className="kpi-value">{m.orders}</strong>
        </div>
        <div className="kpi">
          <span className="kpi-label">销量</span>
          <strong className="kpi-value">{m.units} 件</strong>
        </div>
        <div className="kpi">
          <span className="kpi-label">广告花费</span>
          <strong className="kpi-value">¥{money(m.ad_spend)}</strong>
          <span className="kpi-delta">
            占比 {m.revenue > 0 ? ((m.ad_spend / m.revenue) * 100).toFixed(1) : '0'}%
          </span>
        </div>
        <div className={`kpi ${report.alerts.length ? 'kpi-alert' : ''}`}>
          <span className="kpi-label">预警</span>
          <strong className="kpi-value">{report.alerts.length} 条</strong>
        </div>
      </div>

      {/* 预警列表 */}
      {report.alerts.length > 0 && (
        <div className="alert-list">
          {report.alerts.map((a, i) => (
            <div key={i} className={`alert-item ${a.level}`}>
              <Icon name="alert" size={14} />
              <span>{a.message}</span>
            </div>
          ))}
        </div>
      )}

      <div className="dash-grid">
        {/* 近 14 天 GMV 趋势（纯 CSS 柱状图，零依赖） */}
        <div className="dash-card">
          <p className="dash-title">近 {report.trend.length} 天 GMV 趋势</p>
          <div className="trend-chart">
            {report.trend.map((t) => (
              <div key={t.date} className="trend-col" title={`${t.date}：¥${money(t.revenue)}`}>
                <div
                  className="trend-bar"
                  style={{ height: `${Math.max((t.revenue / maxRevenue) * 100, 3)}%` }}
                />
                <span className="trend-date">{t.date.slice(5)}</span>
              </div>
            ))}
          </div>
        </div>

        {/* 分店铺 */}
        <div className="dash-card">
          <p className="dash-title">分店铺表现</p>
          <table className="mini-table">
            <thead>
              <tr>
                <th>店铺</th>
                <th>GMV</th>
                <th>订单</th>
                <th>销量</th>
              </tr>
            </thead>
            <tbody>
              {report.by_shop.map((s) => (
                <tr key={s.shop}>
                  <td>{s.shop}</td>
                  <td>¥{money(s.revenue)}</td>
                  <td>{s.orders}</td>
                  <td>{s.units}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Top SKU */}
        <div className="dash-card">
          <p className="dash-title">Top SKU（按 GMV）</p>
          <table className="mini-table">
            <thead>
              <tr>
                <th>SKU</th>
                <th>GMV</th>
                <th>库存</th>
              </tr>
            </thead>
            <tbody>
              {report.top_skus.map((s) => (
                <tr key={`${s.shop}-${s.sku}`}>
                  <td>
                    {s.sku}
                    <em className="sku-shop">{s.shop}</em>
                  </td>
                  <td>¥{money(s.revenue)}</td>
                  <td className={s.safe_stock > 0 && s.stock < s.safe_stock ? 'stock-low' : ''}>
                    {s.stock || '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {report.saved_to && (
        <p className="muted sm">日报已归档：{report.saved_to}</p>
      )}
    </section>
  )
}
