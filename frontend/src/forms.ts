import type { FormField } from './types'

const LANG_OPTIONS = [
  { value: 'zh', label: '中文' },
  { value: 'en', label: 'English' },
]

// 每个 Agent 对应的表单字段。新增 Agent 时在这里加一组即可，UI 完全动态渲染。
export const AGENT_FORMS: Record<string, FormField[]> = {
  selection: [
    { key: 'category', label: '品类', type: 'text', placeholder: '如：便携榨汁杯', example: '便携榨汁杯' },
    { key: 'market', label: '目标市场', type: 'text', placeholder: '如：美国 / 欧洲 / 日本', example: '美国' },
    { key: 'budget', label: '预算（可选）', type: 'text', placeholder: '如：首单 3 万人民币', hint: '用于判断进入可行性' },
    { key: 'notes', label: '补充说明', type: 'textarea', placeholder: '任何你关心的点：供应链、竞品、自己的优势…' },
  ],
  listing: [
    { key: 'product', label: '产品', type: 'text', placeholder: '如：不锈钢保温杯 500ml', example: '不锈钢保温杯 500ml' },
    { key: 'features', label: '核心卖点', type: 'textarea', placeholder: '每行一个卖点：12h 保温 / 一键开盖 / 防漏…' },
    { key: 'lang', label: '生成语言', type: 'select', options: LANG_OPTIONS },
  ],
  review: [
    { key: 'product', label: '产品', type: 'text', placeholder: '如：无线蓝牙耳机', example: '无线蓝牙耳机' },
    { key: 'reviews', label: '评论文本', type: 'textarea', placeholder: '把 Amazon / 速卖通的真实评论粘进来，越多越好', hint: '将用于情感分析与痛点聚类' },
    { key: 'notes', label: '补充说明', type: 'textarea', placeholder: '你最想解决的体验问题' },
  ],
  ads: [
    { key: 'acos', label: 'ACOS', type: 'number', placeholder: '0.42 表示 42%', example: '0.42', hint: '>35% 视为亏损风险' },
    { key: 'ctr', label: 'CTR 点击率', type: 'number', placeholder: '0.003 表示 0.3%', example: '0.003', hint: '<0.4% 偏低' },
    { key: 'cvr', label: 'CVR 转化率', type: 'number', placeholder: '0.06 表示 6%', example: '0.06', hint: '<8% 偏低' },
    { key: 'spend', label: '日花费（可选）', type: 'text', placeholder: '如：$50' },
    { key: 'sales', label: '日销售额（可选）', type: 'text', placeholder: '如：$120' },
    { key: 'notes', label: '补充说明', type: 'textarea', placeholder: '投放了多久、主要想解决什么' },
  ],
  logistics: [
    { key: 'destination', label: '目的地', type: 'text', placeholder: '如：美国洛杉矶', example: '美国洛杉矶' },
    { key: 'weight', label: '单件/总重', type: 'text', placeholder: '如：12kg / 500g 每件' },
    { key: 'volume', label: '体积（可选）', type: 'text', placeholder: '如：0.08 cbm' },
    { key: 'quantity', label: '数量', type: 'text', placeholder: '如：800 件' },
    { key: 'notes', label: '时效 / 预算要求', type: 'textarea', placeholder: '如：要在黑五前到仓 / 预算压到最低' },
  ],
  support: [
    { key: 'question', label: '客户问题', type: 'textarea', placeholder: '客户原话，尽量保留情绪', example: '你们的产品用了一周就坏了，必须全额退款不然我给差评！' },
    { key: 'order_info', label: '订单信息（可选）', type: 'text', placeholder: '如：订单 #A123，已发货 5 天' },
    { key: 'lang', label: '回复语言', type: 'select', options: LANG_OPTIONS },
  ],
}

// 后端新增 Agent 时的兜底展示配置。
// 之前 App.tsx 直接写 AGENT_META[active]，后端一旦多注册一个 Agent，
// 这里取到 undefined，接着读 meta.icon 就整页白屏 ——
// 而项目 README 恰恰宣称"新增 Agent 不需要改前端"。
export const FALLBACK_AGENT_META = {
  label: '智能体',
  tagline: '通用分析',
  color: '#64748b',
  icon: 'spark',
}

export function getAgentMeta(agent: string) {
  const known = AGENT_META[agent]
  if (known) return known
  return { ...FALLBACK_AGENT_META, label: agent || FALLBACK_AGENT_META.label }
}

// 未预置表单的 Agent（后端新注册）走这个通用输入框，保证"新增 Agent 前端零改动"
export const GENERIC_FIELD: FormField = {
  key: 'input',
  label: '输入内容',
  type: 'textarea',
  placeholder: '粘贴你要分析的内容，或直接描述你的问题',
  hint: '该智能体未预置表单，用自由文本输入',
}

export function getAgentForms(agent: string): FormField[] {
  return AGENT_FORMS[agent] || []
}

export function defaultPayload(agent: string): Record<string, string> {
  const fields = AGENT_FORMS[agent] || []
  const payload: Record<string, string> = {}
  for (const f of fields) {
    payload[f.key] = ''
  }
  return payload
}

// 一键填入的演示数据：现场演示不用手打字，点一下直接跑
export const AGENT_EXAMPLES: Record<string, Record<string, string>> = {
  selection: {
    category: '便携榨汁杯',
    market: '美国',
    budget: '首单 3 万人民币',
    notes: '工厂在宁波，有现成模具，想做差异化杯型',
  },
  listing: {
    product: '不锈钢保温杯 500ml',
    features: '12 小时长效保温\n一键弹盖，单手可开\n食品级 304 内胆\n双重防漏密封圈\n磨砂防滑杯身',
    lang: 'en',
  },
  review: {
    product: '无线蓝牙耳机',
    reviews:
      '音质真的很惊艳，低音很足，戴一天也不累。\n连接很稳，没有断连过。\n但是电池太不耐用了，半天就没电。\n充电盒盖子太松，放包里会自己打开。\n说明书全是英文，看不懂怎么配对。\n第三次买了，送朋友都说好。\n左耳那只声音比右耳小，是不是次品？\n物流太慢了，等了十几天。',
    notes: '想知道负面评论主要集中在哪，优先改什么',
  },
  ads: {
    acos: '0.42',
    ctr: '0.003',
    cvr: '0.06',
    spend: '$50',
    sales: '$120',
    notes: '自动广告投放三周，主要靠广泛匹配',
  },
  logistics: {
    destination: '美国洛杉矶',
    weight: '12kg / 箱',
    quantity: '800 件',
    notes: '要在黑五前到仓，预算尽量压低',
  },
  support: {
    question: '你们的产品用了一周就坏了，必须全额退款不然我给差评！',
    order_info: '订单 #A123，已发货 5 天',
    lang: 'zh',
  },
}

// 智能体视觉标识：图标 key + 主题色 + 一句话定位
export const AGENT_META: Record<
  string,
  { label: string; tagline: string; color: string; icon: string }
> = {
  selection: { label: '选品分析', tagline: '能不能做，值不值得做', color: '#5b8def', icon: 'target' },
  listing: { label: 'Listing 生成', tagline: '标题 · 五点 · 关键词', color: '#8b5cf6', icon: 'doc' },
  review: { label: '评论洞察', tagline: '差评里藏着改品方向', color: '#06b6d4', icon: 'chat' },
  ads: { label: '广告诊断', tagline: '钱烧在哪，怎么止损', color: '#f59e0b', icon: 'chart' },
  logistics: { label: '物流方案', tagline: '渠道对比与成本时效', color: '#10b981', icon: 'truck' },
  support: { label: '客服话术', tagline: '合规回复 · 升级判断', color: '#f43f5e', icon: 'shield' },
}
