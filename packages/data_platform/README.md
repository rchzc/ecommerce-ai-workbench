# ecom-data-platform · AI 数据中台

跨境电商 AI 生态的**数据层**。负责"数据从哪里来、变成什么形状、怎么流出去"——
不含任何 Agent 与模型编排，那一层在共享集群和运行时底座里。

本仓库对应生态架构图中的 **项目三 · AI 数据中台**。

```bash
python demo.py     # 不需要 API Key，不联网，直接跑
pytest             # 81 个用例，全程离线
```

---

## 一、在生态里的位置

```
        ┌──────────────────────────────────────────────────────┐
        │  业务层（各自独立仓库，只依赖本包 + 共享集群）            │
        │  售前咨询 Agent │ 内容运营 Agent │ 销售考核系统          │
        └───────────────────────┬──────────────────────────────┘
                                │  读本包产出的语料与指标
        ┌───────────────────────▼──────────────────────────────┐
        │  ecom-data-platform（本仓库）                          │
        │                                                      │
        │  connectors/  数据接入  Shopify / Amazon SP-API        │
        │                        飞书多维表格（出口）             │
        │  tabular.py   表格管道  任意嵌套 JSON → 二维表          │
        │  validators.py 规则校验  pass / warn / fail 三档        │
        │  report.py    指标聚合  日报 + 4 条规则预警（零 token）  │
        │  batch.py     批量任务  逐行独立成败 · 幂等键 · 可断点   │
        │  pipeline.py  每日流水线 定时编排 · 幂等判重 · 失败留痕   │
        └───────────────────────┬──────────────────────────────┘
                                │  pip install ecom-agent-shared
        ┌───────────────────────▼──────────────────────────────┐
        │  ecom-agent-shared（共享集群）                          │
        │  LLM 网关 · RAG 检索 · 记忆 · Prompt 中心 · MCP 工具协议 │
        └──────────────────────────────────────────────────────┘
```

**为什么本包也依赖共享集群**：日报的"模型解读"是可选项（默认关），
但一旦开启就要走统一网关；知识库运维也要用共享层的切分与检索。
与其自己再写一套，不如依赖已经过测试的那一层 —— 这正是分层的价值。

---

## 二、五块能力

### 1. 数据接入：连接器只做「拉取 → 清洗归一 → 落盘」

数据入口两个（Shopify Admin API、Amazon SP-API），数据出口一个（飞书多维表格）。
出入口都齐了才叫"系统间数据打通"，只有入口那是半个系统。

```python
from data_platform.connectors.shopify import ShopifyConnector, mock_orders, mock_reviews

connector = ShopifyConnector(shop="your-shop", token="...")
connector.write_docs(mock_orders(), mock_reviews(), docs_dir="data/docs")
# → {'orders': 2, 'reviews': 3, 'written': ['support/live_orders.md', 'review/live_reviews.md']}
```

归一后的文档直接落在 `data/docs/<业务域>/`，知识库重建索引时按目录自动认域 ——
**所以「接一个新平台」= 新增一个连接器文件，业务代码零改动。**

一个刻意的取舍：Shopify 的评论走 CSV 导出，不走 API。因为 Shopify 原生 Admin API
**没有评论接口**（要装第三方 App）。伪造一个不存在的端点比少写一个功能更糟。

### 2. 表格管道与规则校验

运营传上来的 Excel、模型吐出来的 JSON，形状完全不同，但都要变成表格。

```python
from data_platform.tabular import flatten
from data_platform.validators import validate_output

flatten({"title": "折叠伞", "keywords": {"core": ["umbrella"]}})
# → {'title': '折叠伞', 'keywords.core': '["umbrella"]'}

validate_output({"title": "N/A", ...}, schema).status   # → 'warn'（命中占位内容规则）
```

三档结论不是装饰：`pass` 直接入库、`warn` 入库但标记需复核、`fail` **拦下不写库**。
`fail` 对应"缺失过半字段"，意味着输出结构整体崩塌，不是个别字段问题。

### 3. 日报聚合：4 条规则预警，零模型成本

```
- 库存预警：SKU 当前库存 < 安全库存                          → high
- 销量骤降：当日 GMV 环比前一日跌幅 > 30%（按店铺维度）        → mid
- 差评率预警：SKU 当日差评率 > 10%（差评数/评论数）            → mid
- 广告占比预警：店铺广告花费 / GMV > 30%                       → mid
```

这四条都能解释**为什么报警**，且不花一分钱 token。只有规则覆盖不了的模糊判断
才交给模型 —— 这是刻意取舍，不是没做完。产出同时落 `data/reports/daily_<日期>.md`，
落盘失败只记日志不阻断返回（看板该渲染还得渲染）。

### 4. 批量任务：一次几百行，逐行独立成败

```python
batch = BatchService(agent_service=agent_service, max_rows=200, concurrency=3)
job = batch.create_job("listing", rows, idempotency_key="daily-2026-09-18")
# → status=partial  总计 3 行 · 成功 2 · 失败 1
```

- **单行失败不拖垮整批**：第 2 行的模型调用超时，只让第 2 行变成"死信行"，其余照常出结果
- **幂等键防重复扣费**：n8n 重试、前端连点、网络重传都不会触发第二次模型调用
- **可断点**：任务状态落盘 JSON，重启后任务仍可查、可导出
- **导出即运营的表**：输入列保持运营自己的表头，AI 输出统一加 `out.` 前缀，
  外加 `行号 / 处理状态 / 校验结果 / 校验提示 / 知识来源 / 错误信息`

### 5. 每日流水线：刷新 → 日报 →（可选）模型解读 → 推飞书

```python
pipeline = PipelineService(settings, report_service=ReportService(settings, feishu=...))
await pipeline.run(force=True)
# → status=ok   refresh:ok（追加 3 行）  report:ok  feishu:ok
await pipeline.run()
# → skipped=True  今日日报已同步，幂等跳过
```

三个关键决策：

| 决策 | 原因 |
|---|---|
| **幂等优先** | 调度器每天触发一次 + 人手点 N 次，都不能产生重复日报。数据刷新按日期判重，飞书同步按状态文件判重 |
| **失败不中断服务** | 任何一步失败都记录进状态文件并返回结构化结果。今天的失败不该拖垮明天 9 点的定时任务 |
| **调度器极简** | asyncio 单协程算出距下次触发的秒数后睡眠。单机单任务场景，不值得引入 APScheduler |

---

## 三、快速开始

```bash
# 1. 拿到共享集群（本项目依赖它）
git clone https://github.com/rchzc/ecom-agent-shared.git

# 2. 跑演示与测试（都不联网、不需要 Key）
cd packages/data_platform
python demo.py
pytest
```

没配 `.env` 也能跑：`demo.py` 与 `pytest` 会自己把 provider 设成 `mock`、
检索后端设成 `lexical`。要接真实平台或推飞书时再复制 `.env.example`。

---

## 四、目录结构

```
packages/data_platform/
├── data_platform/
│   ├── config.py           集中配置与路径常量（含 Settings 继承共享层）
│   ├── errors 相关         复用 ecom_shared.errors 的类型化错误体系
│   ├── connectors/         shopify.py · amazon_sp_api.py · feishu_bitable.py
│   ├── tabular.py          嵌套结构 → 二维表
│   ├── validators.py       pass / warn / fail 规则校验
│   ├── report.py           日报聚合 + 4 条预警 + markdown 渲染
│   ├── batch.py            批量任务编排（幂等 · 并发 · 落盘）
│   └── pipeline.py         每日流水线 + 定时调度
├── data/
│   ├── docs/               知识库语料 26 篇 / 6 个业务域（随包发布）
│   ├── sales/              样例销售明细
│   └── reports/ jobs/      运行期产物（.gitignore 排除）
├── tests/                  76 个用例 / 5 个文件
├── demo.py                 七步离线演示
├── _bootstrap.py           开发态 sys.path 引导（不随包发布）
└── pyproject.toml
```

---

## 五、几个刻意的设计决定

| 决定 | 原因 |
|---|---|
| **路径常量只有一个来源**（`config.py`） | 老代码 7 个模块各自按"文件在目录树里的深度"算基准目录，包一挪位置就**静默**读错目录。测试里有一条结构性守卫防止写回去 |
| **飞书凭证不齐时返回 503，不降级成 mock** | 静默降级会把"其实没连通"伪装成"同步成功"，是 Demo 项目最常见的坑 |
| **列表摊平时整体存 JSON，不逐项展开** | 元素本身是对象时，展开成 `c.0.d` 会彻底散架，下游拼不回去 |
| **下划线开头的输入键不进导出表** | `__index` 是调用方与 Agent 之间的内部约定，混进表格运营会当成脏列 |
| **单行失败保留在结果里，不整批回滚** | 运营要的是"哪几行有问题"，不是"整批重来" |
| **规则预警优先于模型解读** | 规则可解释、零成本、结果稳定；模型解读是加分项，默认关闭 |
| **测试全程离线**（`provider=mock` + `vector_backend=lexical`） | 需要网络的测试会变成"本地能过、CI 挂掉"，最后没人跑 |
| **自动隔离模块级路径**（autouse fixture） | 否则跑一次测试就往仓库 `data/` 里写文件，第二次运行断言神秘失败 |
| **不引 `pytest-asyncio`** | 只是"把协程跑到结束"，插件与 pytest 版本错位时失败信息会指向插件而非被测代码 |

---

## 六、相关仓库

| 仓库 | 生态位置 |
|---|---|
| **[ecom-agent-shared](https://github.com/rchzc/ecom-agent-shared)** | 共享集群：LLM 网关 / RAG / 记忆 / Prompt 中心 / MCP 工具协议 |
| **[ecom-agent-runtime](https://github.com/rchzc/ecom-agent-runtime)** | Agent 运行时底座：ReAct Loop / LangGraph / 意图路由 / 轨迹自进化 |
| **[ecommerce-ai-workbench](https://github.com/rchzc/ecommerce-ai-workbench)** | 业务应用层与单容器交付（本包的宿主仓库） |

三者同属「跨境电商 AI 电商工作台」生态，按层拆分，依赖方向：
**业务应用 → 运行时底座 → 共享集群**。

---

## 七、已知边界

写清楚没做什么，比假装都做了更可靠：

- **连接器的真实分支没跑过真店铺**。SP-API / Admin API 的代码在，
  但需要商家授权才能验证。仓库只保证 mock 路径端到端跑通。
- **批量任务状态存进程内存 + 落盘 JSON**，多实例部署要换任务队列（Redis / Celery）。
- **飞书同步按天判重，不做行级增量**：改一行数据要重跑当天，会覆盖整行。
- **预警阈值写在 `report.py` 的 `RULES` 常量里**，改阈值要发版，没做配置化。
- **语料是项目自带的 26 篇**，不是真实店铺沉淀。它的作用是把链路跑通、
  让检索有可验证的召回率，不是"我们有海量数据"。
- **没有鉴权**。本包是库不是服务，鉴权在外层 FastAPI 应用里。

---

## License

MIT
