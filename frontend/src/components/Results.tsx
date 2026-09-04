import { useState } from 'react'
import { Icon } from './Icons'

// 结果数据来自模型输出，字段可能缺失或类型不符 —— 一律做安全兜底
type D = Record<string, unknown>

const asStr = (v: unknown) => (typeof v === 'string' ? v : v == null ? '' : String(v))
const asNum = (v: unknown) => (typeof v === 'number' && !Number.isNaN(v) ? v : Number(v) || 0)
const asArr = (v: unknown): unknown[] => (Array.isArray(v) ? v : [])

export function AgentResultView({ agent, data }: { agent: string; data: D }) {
  switch (agent) {
    case 'selection':
      return <SelectionResult d={data} />
    case 'listing':
      return <ListingResult d={data} />
    case 'review':
      return <ReviewResult d={data} />
    case 'ads':
      return <AdsResult d={data} />
    case 'logistics':
      return <LogisticsResult d={data} />
    case 'support':
      return <SupportResult d={data} />
    default:
      return <GenericResult d={data} />
  }
}

// ---------------------------------------------------------------- 通用零件

function CopyBtn({ text, label = '复制' }: { text: string; label?: string }) {
  const [ok, setOk] = useState(false)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setOk(true)
      setTimeout(() => setOk(false), 1600)
    } catch {
      /* 剪贴板不可用时静默失败，不打断演示 */
    }
  }
  return (
    <button className="copy-btn" onClick={copy}>
      <Icon name={ok ? 'check' : 'copy'} size={14} />
      {ok ? '已复制' : label}
    </button>
  )
}

function Section({
  title,
  icon,
  tone,
  children,
  action,
}: {
  title: string
  icon?: string
  tone?: 'good' | 'warn' | 'bad'
  children: React.ReactNode
  action?: React.ReactNode
}) {
  return (
    <section className={`rsec ${tone || ''}`}>
      <header className="rsec-head">
        <h4>
          {icon && <Icon name={icon} size={15} />}
          {title}
        </h4>
        {action}
      </header>
      <div className="rsec-body">{children}</div>
    </section>
  )
}

function Bullets({ items, tone = 'plain' }: { items: unknown[]; tone?: 'good' | 'bad' | 'plain' | 'num' }) {
  const list = items.map(asStr).filter(Boolean)
  if (!list.length) return <p className="muted">—</p>
  return (
    <ul className={`bullets ${tone}`}>
      {list.map((t, i) => (
        <li key={i}>
          {tone === 'num' ? <span className="bnum">{i + 1}</span> : <Icon name={tone === 'bad' ? 'alert' : 'check'} size={14} />}
          <span>{t}</span>
        </li>
      ))}
    </ul>
  )
}

// ---------------------------------------------------------------- 选品

function SelectionResult({ d }: { d: D }) {
  const score = Math.max(0, Math.min(100, asNum(d.score)))
  const verdict = asStr(d.verdict)
  const comp = (d.competition || {}) as D
  const compLevel = asStr(comp.level)
  const tone =
    verdict.includes('不建议') ? 'bad' : verdict.includes('谨慎') ? 'warn' : 'good'
  // 评分环：半径 42，周长约 264
  const R = 42
  const C = 2 * Math.PI * R
  const offset = C * (1 - score / 100)
  const ringColor = score >= 70 ? 'var(--green)' : score >= 45 ? 'var(--amber)' : 'var(--rose)'

  return (
    <div className="result-stack">
      <div className="hero-score">
        <div className="ring-wrap">
          <svg width="104" height="104" viewBox="0 0 104 104">
            <circle cx="52" cy="52" r={R} className="ring-bg" />
            <circle
              cx="52"
              cy="52"
              r={R}
              className="ring-fg"
              stroke={ringColor}
              strokeDasharray={C}
              strokeDashoffset={offset}
            />
          </svg>
          <div className="ring-text">
            <strong>{score}</strong>
            <span>/ 100</span>
          </div>
        </div>
        <div className="hero-meta">
          <span className={`verdict ${tone}`}>
            <Icon name={tone === 'good' ? 'check' : 'alert'} size={15} />
            {verdict || '—'}
          </span>
          {compLevel && (
            <span className={`chip lvl-${compLevel === '高' ? 'bad' : compLevel === '中' ? 'warn' : 'good'}`}>
              竞争强度：{compLevel}
            </span>
          )}
          {asStr(comp.reason) && <p className="hero-note">{asStr(comp.reason)}</p>}
        </div>
      </div>

      {asStr(d.summary) && <p className="summary-lead">{asStr(d.summary)}</p>}

      <div className="grid-2">
        <Section title="市场机会" icon="spark" tone="good">
          <Bullets items={asArr(d.opportunities)} tone="good" />
        </Section>
        <Section title="主要风险" icon="alert" tone="bad">
          <Bullets items={asArr(d.risks)} tone="bad" />
        </Section>
      </div>

      <Section title="下一步行动" icon="arrow">
        <Bullets items={asArr(d.action_items)} tone="num" />
      </Section>
    </div>
  )
}

// ---------------------------------------------------------------- Listing

function ListingResult({ d }: { d: D }) {
  const title = asStr(d.title)
  const kw = (d.keywords || {}) as D
  return (
    <div className="result-stack">
      {title && (
        <section className="listing-title">
          <header>
            <h4>
              <Icon name="doc" size={15} />
              产品标题
            </h4>
            <CopyBtn text={title} />
          </header>
          <p className="title-text">{title}</p>
          <footer className="char-count">{title.length} 字符</footer>
        </section>
      )}

      <Section title="五点描述" icon="check" tone="good">
        <Bullets items={asArr(d.bullets)} tone="num" />
      </Section>

      <Section title="关键词布局" icon="target">
        {asArr(kw.core).length > 0 && (
          <>
            <p className="kw-label">核心词</p>
            <div className="chips">
              {asArr(kw.core).map((k, i) => (
                <span className="chip solid" key={i}>
                  {asStr(k)}
                </span>
              ))}
            </div>
          </>
        )}
        {asArr(kw.long_tail).length > 0 && (
          <>
            <p className="kw-label">长尾词</p>
            <div className="chips">
              {asArr(kw.long_tail).map((k, i) => (
                <span className="chip" key={i}>
                  {asStr(k)}
                </span>
              ))}
            </div>
          </>
        )}
      </Section>

      {asStr(d.a_plus) && (
        <section className="aplus">
          <header>
            <h4>
              <Icon name="book" size={15} />
              A+ 品牌故事
            </h4>
            <CopyBtn text={asStr(d.a_plus)} />
          </header>
          <p>{asStr(d.a_plus)}</p>
        </section>
      )}

      {asArr(d.tips).length > 0 && (
        <Section title="优化提示" icon="spark">
          <Bullets items={asArr(d.tips)} />
        </Section>
      )}
    </div>
  )
}

// ---------------------------------------------------------------- 评论洞察

function ReviewResult({ d }: { d: D }) {
  const s = (d.sentiment || {}) as D
  const pos = asNum(s.positive)
  const neu = asNum(s.neutral)
  const neg = asNum(s.negative)
  const total = pos + neu + neg || 1
  const bars = [
    { label: '正面', v: pos, cls: 'good' },
    { label: '中性', v: neu, cls: 'mid' },
    { label: '负面', v: neg, cls: 'bad' },
  ]

  return (
    <div className="result-stack">
      <section className="sentiment">
        <header>
          <h4>
            <Icon name="chat" size={15} />
            情感分布
          </h4>
        </header>
        <div className="sbar-row">
          {bars.map((b) => (
            <div className="sbar-seg" key={b.label} style={{ width: `${(b.v / total) * 100}%` }}>
              <span className={`sbar ${b.cls}`} />
            </div>
          ))}
        </div>
        <div className="slegend">
          {bars.map((b) => (
            <span key={b.label}>
              <i className={`dot ${b.cls}`} />
              {b.label} {b.v}%
            </span>
          ))}
        </div>
      </section>

      {asStr(d.summary) && <p className="summary-lead">{asStr(d.summary)}</p>}

      <Section title="痛点聚类" icon="alert" tone="bad">
        <div className="pain-list">
          {asArr(d.pain_points).map((p, i) => {
            const pp = (p || {}) as D
            const type = asStr(pp.type)
            return (
              <div className="pain" key={i}>
                <div className="pain-head">
                  <strong>{asStr(pp.topic)}</strong>
                  <span className={`chip ${type.includes('产品') ? 'bad' : type.includes('物流') ? 'warn' : 'mid'}`}>
                    {type || '未分类'}
                  </span>
                  <span className="pain-count">{asNum(pp.count)} 次</span>
                </div>
                {asStr(pp.quote) && <blockquote className="quote">{asStr(pp.quote)}</blockquote>}
              </div>
            )
          })}
          {asArr(d.pain_points).length === 0 && <p className="muted">—</p>}
        </div>
      </Section>

      <div className="grid-2">
        <Section title="产品改进" icon="target" tone="good">
          <Bullets items={asArr(d.product_actions)} tone="good" />
        </Section>
        <Section title="Listing 优化" icon="doc" tone="good">
          <Bullets items={asArr(d.listing_actions)} tone="good" />
        </Section>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- 广告

function AdsResult({ d }: { d: D }) {
  const verdict = asStr(d.verdict)
  const tone = verdict.includes('严重') ? 'bad' : verdict.includes('需优化') ? 'warn' : 'good'
  return (
    <div className="result-stack">
      <div className="verdict-bar">
        <span className={`verdict ${tone}`}>
          <Icon name={tone === 'good' ? 'check' : 'alert'} size={16} />
          {verdict || '—'}
        </span>
        {asStr(d.summary) && <p>{asStr(d.summary)}</p>}
      </div>

      <Section title="问题定位" icon="alert" tone="bad">
        <div className="issue-list">
          {asArr(d.issues).map((it, i) => {
            const o = (it || {}) as D
            const sev = asStr(o.severity)
            return (
              <div className="issue" key={i}>
                <span className={`sev sev-${sev === '高' ? 'bad' : sev === '中' ? 'warn' : 'low'}`}>{sev || '-'}</span>
                <div>
                  <strong>{asStr(o.area)}</strong>
                  <p>{asStr(o.description)}</p>
                </div>
              </div>
            )
          })}
          {asArr(d.issues).length === 0 && <p className="muted">未定位到明显异常</p>}
        </div>
      </Section>

      <Section title="优化动作（按优先级）" icon="bolt">
        <div className="table">
          <div className="tr thead">
            <span>动作</span>
            <span>优先级</span>
            <span>预期效果</span>
            <span>见效周期</span>
          </div>
          {asArr(d.actions).map((a, i) => {
            const o = (a || {}) as D
            const p = asStr(o.priority)
            return (
              <div className="tr" key={i}>
                <span className="t-main">{asStr(o.action)}</span>
                <span>
                  <span className={`pri pri-${p === 'P0' ? 'bad' : p === 'P1' ? 'warn' : 'low'}`}>{p || '-'}</span>
                </span>
                <span className="t-dim">{asStr(o.expected_effect)}</span>
                <span className="t-dim">{asStr(o.eta)}</span>
              </div>
            )
          })}
        </div>
      </Section>

      {asStr(d.budget_advice) && (
        <section className="advice">
          <h4>
            <Icon name="chart" size={15} />
            预算调整建议
          </h4>
          <p>{asStr(d.budget_advice)}</p>
        </section>
      )}
    </div>
  )
}

// ---------------------------------------------------------------- 物流

function LogisticsResult({ d }: { d: D }) {
  const options = asArr(d.options)
  const rec = asStr(d.recommended)
  return (
    <div className="result-stack">
      <Section title="渠道对比" icon="truck">
        <div className="opt-grid">
          {options.map((o, i) => {
            const opt = (o || {}) as D
            const isRec = asStr(opt.channel) === rec
            return (
              <div className={`opt ${isRec ? 'rec' : ''}`} key={i}>
                <div className="opt-head">
                  <strong>{asStr(opt.channel)}</strong>
                  {isRec && (
                    <span className="rec-badge">
                      <Icon name="check" size={12} />
                      推荐
                    </span>
                  )}
                </div>
                <div className="opt-metrics">
                  <div>
                    <span>成本</span>
                    <strong>{asStr(opt.cost_estimate)}</strong>
                  </div>
                  <div>
                    <span>时效</span>
                    <strong>{asStr(opt.eta)}</strong>
                  </div>
                </div>
                <div className="opt-pros">
                  <Bullets items={asArr(opt.pros)} tone="good" />
                  <Bullets items={asArr(opt.cons)} tone="bad" />
                </div>
              </div>
            )
          })}
        </div>
      </Section>

      {rec && (
        <div className="advice">
          <h4>
            <Icon name="check" size={15} />
            推荐 {rec}
          </h4>
          <p>{asStr(d.reason)}</p>
        </div>
      )}

      <Section title="风险提示" icon="alert" tone="bad">
        <Bullets items={asArr(d.risks)} tone="bad" />
      </Section>

      {asStr(d.summary) && <p className="summary-lead">{asStr(d.summary)}</p>}
    </div>
  )
}

// ---------------------------------------------------------------- 客服

function SupportResult({ d }: { d: D }) {
  const reply = asStr(d.reply)
  const escalate = d.escalate === true
  return (
    <div className="result-stack">
      {reply && (
        <section className="reply-card">
          <header>
            <h4>
              <Icon name="chat" size={15} />
              可直接发送的回复
            </h4>
            <div className="reply-actions">
              {asStr(d.tone) && <span className="chip mid">语气：{asStr(d.tone)}</span>}
              <CopyBtn text={reply} label="复制话术" />
            </div>
          </header>
          <p className="reply-text">{reply}</p>
        </section>
      )}

      {escalate && (
        <div className="escalate">
          <Icon name="alert" size={17} />
          <div>
            <strong>需人工介入</strong>
            <p>{asStr(d.escalate_reason)}</p>
          </div>
        </div>
      )}

      <Section title="处理要点" icon="check" tone="good">
        <Bullets items={asArr(d.key_points)} tone="good" />
      </Section>

      {asStr(d.follow_up) && (
        <section className="advice">
          <h4>
            <Icon name="arrow" size={15} />
            后续跟进
          </h4>
          <p>{asStr(d.follow_up)}</p>
        </section>
      )}
    </div>
  )
}

// ---------------------------------------------------------------- 兜底

function GenericResult({ d }: { d: D }) {
  return (
    <div className="result-stack">
      <pre className="stream-text">{JSON.stringify(d, null, 2)}</pre>
    </div>
  )
}
