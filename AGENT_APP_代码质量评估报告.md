# 跨境电商 AI 运营工作台 · 代码质量评估与优化报告

> 评估对象：`ecommerce-ai-workbench`（RAG + 多 Agent 的跨境电商运营工作台）
> 范围：后端 Python（FastAPI，~2.8k 行）+ 前端 React/TS（~1.7k 行）
> 方式：通读全部核心模块 → 静态问题定位 → 修复 → 单测 + 运行冒烟验证

---

## 一、整体评价

架构思路成熟，分层（api / services / agents / core）清晰，文档与注释质量在同类 Demo 项目里属上乘——多厂商抽象、模型路由、三级 JSON 容错、本地重排、结构化日志都被写进了代码并有说明。

**但"声明"与"实现"之间存在若干不一致**：部分 README / 注释宣称的能力（如"配置缺失即失败""新增 Agent 不改前端""本地降级可用"），实现层存在缺陷，导致声明成立、行为却偏差。本次优化以"让实现配得上声明、并堵住会崩的边界"为主线。

---

## 二、发现的问题（按严重度）

### P0 — 会出错或误导排查
| # | 问题 | 位置 | 后果 |
|---|------|------|------|
| 1 | 配置校验失败时所有 `/api/*` 返回 500 而非 503 | `api/deps.py` | 缺 Key 这种用户可自行修的错，被报成"服务内部错误"，且拿不到可读原因 |
| 2 | 前端 Agent 列表硬编码 `AGENT_META`，与 `fetchAgents` 脱节 | `frontend/src/App.tsx` | 后端新增 Agent 直接白屏；`fetchAgents` 成死代码，README"改后端即可"的承诺不成立 |
| 3 | 流式结束但 JSON 解析失败时 `setState('done')` 且无 data | `frontend/src/App.tsx` | 页面永久卡在打字机动画，用户不知成功还是失败 |
| 4 | 文档切分对**每个**块无差别补上前一块尾部 | `core/rag.py` | 中文文档大多整段 <600 字，知识库几乎每个切片都被上一段污染，向量被稀释、召回精度下降 |

### P1 — 正确性与健壮性
| # | 问题 | 位置 | 后果 |
|---|------|------|------|
| 5 | LLM 流式调用**任何异常**都当"不支持 JSON 模式"重试 | `core/llm.py` | 401/429/超时也被伪装成降级，多花一次注定失败的调用、掩盖真故障 |
| 6 | 本地 embedding 降级时用**空串**做文本检索 | `core/vectorstore.py` / `embeddings.py` | 返回的是库里排在最前的任意切片，"降级可用"实为"降级失效" |
| 7 | `KnowledgeService.rebuild` 抛 `RuntimeError` | `services/agent_service.py` | 被兜底成 500，但"未找到文档"是用户可修问题，应 503 + 明确原因 |
| 8 | `config.py` 与 `errors.py` 各定义一份 `ConfigError` | 同名两类 | 捕获其一时另一类漏网，全局错误处理器可能失效 |
| 9 | `main.py` 多处直接 `os.getenv`（CORS/日志/路径） | `main.py` | 违背项目自身"配置集中在 config.py"原则，且 CORS 两处规则易漂移 |
| 10 | 控制器绕过服务层直接 `from ..agents import list_agents` | `api/routes.py` | 分层单向依赖被悄悄开捷径，加权限过滤时易漏 |
| 11 | `ads.precheck` 用 `isinstance(v, (int, float))` | `agents/ads.py` | `True`（bool 是 int 子类）被当成 1.0，误判"ACOS 100% 严重亏损" |
| 12 | 路由回调里重复"建 Agent + KeyError 处理" | `services/agent_service.py` | 两处文案易漂移、易漏改 |

### P2 — 工程整洁
| # | 问题 | 位置 |
|---|------|------|
| 13 | 死代码：`usage_snapshot`、`schemas.AgentRequest.stream` 字段、前端 `runAgent`/`fetchAgents` 未用 |
| 14 | `rerank` 在串内块（非跨段）场景下重叠被重复应用一次（随 #4 一并修正） |
| 15 | 缺测试：项目零单测；缺 dev 依赖清单 |

---

## 三、已完成的修复

**配置与错误体系**
- `config.py`：复用 `errors.ConfigError`（消除双定义）；新增 `log_level / chroma_dir / static_dir / cors_origins` 到 `Settings`，并抽出 `load_cors_origins/resolve_chroma_dir/resolve_static_dir/load_log_level` 统一入口。
- `api/deps.py`：组件缺失时区分"配置错误 / 未初始化"，一律 503 + 可读原因（**修复 #1**）。
- `main.py`：CORS / 日志 / 路径全部改走 `config.py`；CORS 为 `*` 时留痕警告；新增 `/api/ready` 就绪探针（不依赖任何组件，配置错也返回 200 + ready=false）。

**前端（#2/#3）**
- Agent 列表改为**以 `/api/agents` 为准**，拉不到才退回内置清单；取不到 meta 时用兜底样式，**不再白屏**；后端新增 Agent 走通用输入框，真正"零改前端"。
- 流式解析失败 → 明确报错并附原始输出；流式通道异常 → 自动降级非流式重试。SSE 解析兼容 `\r\n\r\n`。

**检索与网关（#4/#5/#6）**
- `rag.py`：重叠只在"长段被切断"处应用，独立段落不再被污染（**修复 #4**）；顺带消除串内块双重重叠。
- `llm.py`：新增 `is_unsupported_json_mode()`，**仅** 4xx 参数类错误才重试；`complete()` 同样具备降级；新增 `resolve_route()` 一次算完 model/tier/score，流式链路不再重复推理。
- `vectorstore.py`：无 embedding 时改传**真实 query 文本**检索；`n_results` 超出域内实际数量时按实际数重试，避免部分 Chroma 版本直接报错。

**服务层与细节（#7/#8/#10/#11/#12）**
- `rebuild` 改用 `ConfigError` / `KnowledgeBaseError`；新增 `_get_agent()` 去重；删死代码 `usage_snapshot`。
- `routes.py`：health 与 agents 统一经 `AgentService`（agents 端点保留直连注册表，确保未就绪时仍可用）。
- `ads.py`：`_as_number()` 显式排除 `bool` 与 `NaN`（**修复 #11**）。

**工程化（#15）**
- 新增 `backend/tests/` 共 **71 个单测**（解析容错 / 切分 / 重排 / 路由 / Agent 预检 / 配置校验 / 服务层错误类型化），新增 `pytest.ini`（asyncio auto）、`conftest.py`、`requirements-dev.txt`。

---

## 四、验证结果

- **后端单测**：`pytest` → **71 passed**（覆盖全部新增/修复逻辑，含两个由测试驱动的缺陷修复）。
- **前端**：`tsc --noEmit` 0 错误；`vite build` 成功（166 KB JS / 18 KB CSS）。
- **运行冒烟**（两个独立实例）：

  | 端点 | 正常配置 | 空 API Key（配置错误） |
  |------|---------|----------------------|
  | /api/ready | 200 ready=true | 200 ready=false + 原因 |
  | /api/agents | 200 | 200（面板可渲染） |
  | /api/health | 200 | **503 + code=config_error + 明确提示** |
  | /api/config/error | 200 | 200 + 中文原因 |

  关键修复 #1 已确认：配置错误从"500 服务内部错误"变为"503 缺失 API Key"。

---

## 五、残留风险与后续建议

1. **Docker 加固**：`Dockerfile` 缺 `HEALTHCHECK`（可接 `/api/ready`）；运行用户未从 root 降权；`docker-compose.yml` 缺 healthcheck。生产部署前补上。
2. **认证 / 限流**：README 已诚实标注——多租户公网部署前需补 JWT、限流、监控告警。
3. **检索增量更新**：当前 `rebuild` 是全量；接真实店铺数据后应改增量，避免每次全量重算。
4. **依赖版本**：`pip freeze` 显示 FastAPI 0.141、`chromadb` 1.5.9（远新于 README 暗示的范围）。建议把 `requirements.txt` 的 `>=` 收紧或锁版，避免 Chroma API 漂移（本次已对 `n_results` 边界做兼容）。
5. **测试广度**：当前为纯函数级单测；建议后续补 `FastAPI TestClient` 集成测试（重点验证 SSE 流式事件顺序、错误码映射）。
