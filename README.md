# 跨境电商 AI 运营工作台

> RAG + 多 Agent 的跨境电商运营决策系统 · 一人全栈交付 · 单容器可跑

面向跨境电商运营（选品 / Listing / 评论 / 广告 / 物流 / 客服）的 AI 工作台。
基于「领域知识库检索 + 大语言模型」的链路，把方法论沉淀成 6 个可独立调用的智能体，
并支持流式输出、模型自动路由、用量统计。

---

## 核心能力

| 智能体 | 做什么 | 输入示例 |
| --- | --- | --- |
| `selection` 选品 | 市场机会 / 竞争强度 / 风险 / 进入建议 | 品类、目标市场、预算 |
| `listing` Listing | 标题、五点、关键词、A+ 文案（多语言） | 产品、卖点、语言 |
| `review` 评论洞察 | 情感分析、痛点聚类、改品建议 | 评论文本 |
| `ads` 广告诊断 | 规则预检 + 模型归因 + 分优先级动作 | ACOS / CTR / CVR |
| `logistics` 物流 | 多渠道对比、成本时效、风险提示 | 目的地、重量、数量 |
| `support` 客服 | 合规话术、情绪安抚、升级判断 | 客户问题、语言 |

每个智能体走同一条链路：**检索领域知识 → 组装 Prompt → 调用模型 → 结构化解析 → 异常兜底**。

## 技术要点（面试可讲）

- **分层架构**：`api`（控制器）/ `services`（业务编排）/ `agents`（领域逻辑）/ `core`（基础设施），
  依赖方向单向，新增智能体只改 `agents/` 一个文件。
- **多厂商抽象层**：百炼 / DeepSeek / OpenAI / Ollama 统一走 OpenAI 兼容协议，切换厂商只改 `.env`，业务代码零改动。
- **模型路由**：按任务复杂度规则自动选轻量 / 重量模型，简单任务不占用大模型额度。
- **三级 JSON 容错解析**：直接解析 → 去 Markdown 围栏 → 截取首个完整对象；三级都失败直接报错，不把脏数据透传前端。
- **RAG**：段落优先 + 句切 + 相邻重叠的三级切分，ChromaDB 持久化，按领域元数据过滤。
- **检索重排（reranker）**：向量召回 top_k×2 候选后，用「语义分 + 词面重叠分」融合重排再取 top_k，
  缓解纯向量召回"字面命中却被排后"的噪声。纯本地、不联网、可复现（见 `core/rerank.py`）。
- **流式输出**：SSE 实现打字机效果，长任务不用干等。
- **工程化**：集中校验配置（缺密钥启动即失败）、类型化错误 → 规范 JSON、请求 ID + 结构化日志、健康检查、单容器交付。

## 快速开始

### 方式一：Windows 一键启动（推荐）

双击项目根目录的 **`启动.bat`**，脚本会自动完成：

1. 检查虚拟环境与依赖
2. 检查 `backend/.env` 中的 API Key（为空会拦截并提示，避免"跑起来却调不通"）
3. 启动后端并自动打开浏览器 `http://127.0.0.1:8000`

> 保持弹出的黑色窗口开启，关闭窗口即停止服务。

首次使用需先按下面的「准备密钥」填好 Key。

### 方式二：命令行启动

```bash
cd backend
.venv/Scripts/activate        # Windows
# 或：source .venv/bin/activate  # macOS / Linux
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### 准备密钥

```bash
cp backend/.env.example backend/.env
# 编辑 backend/.env，把 LLM_API_KEY 填成你的阿里云百炼 API Key
```

> 推荐用**阿里云百炼**：新用户开通送 7000 万 tokens（每模型输入输出各 100 万，有效期 90 天），
> 且自带 `text-embedding-v3` embedding 模型，国内直连。免费额度仅在**华北 2（北京）**地域生效，
> API Base 保持默认 `https://dashscope.aliyuncs.com/compatible-mode/v1` 即可。

### 2. 后端

```bash
cd backend
python -m venv .venv && .venv/Scripts/activate        # Windows
# 或：python3 -m venv .venv && source .venv/bin/activate  # macOS / Linux
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### 3. 重建知识库（首次必须）

知识库向量索引不会自动构建，启动后调一次（或网页里点「重建」）：

```bash
curl -X POST http://127.0.0.1:8000/api/kb/rebuild
```

### 4. 前端（开发态，可选）

```bash
cd frontend
npm install
npm run dev          # http://127.0.0.1:5173 （代理 /api → 8000）
```

生产交付时前端构建产物由 FastAPI 统一托管，无需单独起前端服务。

### 5. 单容器部署

```bash
docker compose build
LLM_API_KEY=sk-xxx docker compose up -d
# 首次部署后执行：curl -X POST http://localhost:8000/api/kb/rebuild
# 打开 http://localhost:8000
```

## API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查（provider / 模型 / 知识库切片数） |
| GET | `/api/agents` | 列出所有智能体（前端动态渲染面板） |
| POST | `/api/agent/{name}` | 执行指定智能体（非流式） |
| POST | `/api/agent/{name}/stream` | 流式执行（SSE，打字机效果） |
| GET | `/api/kb/stats` | 知识库统计 |
| POST | `/api/kb/rebuild` | 重建向量索引 |
| GET | `/api/usage` | 模型用量统计 |

交互式文档：`/api/docs`（Swagger）。

## 目录结构

```
.
├── backend/
│   ├── app/
│   │   ├── api/        控制器（只做请求解析与响应格式化）
│   │   ├── services/   业务编排（Agent 编排 / 知识库管理）
│   │   ├── agents/     6 个智能体，各自实现 build_prompt + output_schema
│   │   ├── core/       LLM 网关 / 向量化 / 切分 / 向量库
│   │   ├── config.py   配置（多厂商 preset + 启动校验）
│   │   ├── errors.py   类型化错误
│   │   ├── logging.py  结构化日志 + 请求 ID
│   │   └── main.py     FastAPI 入口（生命周期 / 中间件 / 静态托管）
│   ├── data/docs/      知识库源文件（21 篇，按领域分目录）
│   └── scripts/        gen_docs.py（生成文档）/ sync_data.py（接入真实店铺数据）/ eval_retrieval.py（检索质量评测）
├── frontend/           React 18 + Vite + TypeScript
├── Dockerfile          多阶段构建，单容器交付
└── docker-compose.yml
```

## 知识库文档

`backend/data/docs/` 下的 21 篇运营方法论由 `backend/scripts/gen_docs.py` 生成，
按 `selection / listing / review / ads / logistics / support` 六个领域分目录。
要扩充知识，编辑脚本里的 `DOCS` 字典后重跑，或直接在对应目录放 `.md` 文件再 `POST /api/kb/rebuild`。

## 接入真实店铺数据（数据连接器）

当前知识库是「文档驱动」：运营把业务文本粘贴进智能体即可出结果。要接客户**真实店铺数据**，
只需新增一个数据连接器，把平台开放 API 的数据拉取、清洗后写入 `data/docs/<domain>/`，
再 `POST /api/kb/rebuild` —— 智能体与 RAG **零改动**（它们只读知识层，不管数据来源）。
这把「人粘贴」升级为「自动拉取」，是项目从 demo 走向生产的关键一步。

### Shopify 连接器（已内置）

```bash
# 演示（无需凭证）：生成 mock 订单/评论写入知识库
python scripts/sync_data.py --provider shopify --mock

# 真实接入：在 backend/.env 填 SHOPIFY_SHOP / SHOPIFY_TOKEN 后
python scripts/sync_data.py --provider shopify
```

- **订单**：走 Shopify REST Admin API（`orders.json`），作为客服 Agent 的真实上下文。
- **评论**：Shopify 原生 API 不含评论，来自评论 App / CSV 导出（`data/import/reviews.csv`，
  字段 `product,rating,content`）。这是真实情况，不伪造 API。
- 写入 `data/docs/support/live_orders.md` 与 `data/docs/review/live_reviews.md`，每次同步覆盖；
  这两个文件已被 gitignore，不会污染仓库。

### 接其它平台

新增 `backend/app/connectors/<platform>.py`，实现同样「拉取 -> 清洗 -> 写 data/docs -> rebuild」
契约即可。亚马逊走 SP-API（需注册应用 + AWS 签名 + OAuth，门槛更高但最普遍），
TikTok Shop / Temu / AliExpress 各有开放平台，逻辑一致。

### 生产化待补（路线图，非当前承诺）

- 定时调度（cron / worker）自动同步，而非手动跑脚本
- 增量更新（只重建变化的切片）而非全量 rebuild
- 接入后补认证（JWT）/ 限流 / 监控告警，才能放公网多租户使用

## 检索质量评测

别只凭"感觉准"。`scripts/eval_retrieval.py` 把检索质量变成可复现的数字：

```bash
python scripts/eval_retrieval.py            # 离线语料覆盖检查（不联网、不需 chromadb）
python scripts/eval_retrieval.py --online   # 再跑在线向量召回，输出 Recall@k（需 LLM Key）
```

- **离线检查**：把 21 篇文档按现有切分切块，在每个业务域内用关键词排序，校验 6 个评测问题的
  期望知识是否落在 top4。当前结果 **6/6 = 100% 覆盖**（证明知识库"覆盖"了这些问题且可达）。
- **在线检查**：真实向量化 → 检索 → rerank 重排 → 校验命中，输出 **Recall@k**。
- 评测集在脚本顶部 `EVAL_QUERIES`，覆盖选品 / Listing / 评论 / 广告 / 物流 / 客服六个域，可增删。
- 诚实边界：这是**检索层**质量评测，不等同端到端回答准确率。
