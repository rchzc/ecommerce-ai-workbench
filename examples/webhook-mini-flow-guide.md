# n8n / Coze 回调 AI 工作台 · Mini 流程说明

> 用途：把简历里写的「已实现对外 Webhook（X-API-Key 鉴权，n8n / Dify / Coze 可回调触发 Agent）」变成一条**可演示、可截图**的真实链路。
> 这条流程本身不依赖大模型是否能跑通——只要后端起来了、配了 `WORKFLOW_API_KEY`，就能用 `curl` 先验证鉴权与契约，再接 n8n / Coze。

---

## 1. Webhook 契约（后端已实现：`backend/app/api/batch.py` → `/hooks/agent/{name}`）

| 项 | 值 |
|----|----|
| 方法 | `POST` |
| 路径 | `/api/hooks/agent/{name}`，`{name}` ∈ `selection / listing / review / ads / logistics / support / replenish` |
| 鉴权 | 请求头 `X-API-Key: <WORKFLOW_API_KEY>`（常量时间比较；未配置 Key 时端点直接 503，不做静默放行） |
| 请求体 | `{ "payload": { ...最多 30 个字段... } }` |
| 正常响应 | `{ ...Agent 结果..., "validation": { "status": "pass"\|"warn"\|"fail", "issues": [...] } }` |
| 关键错误码 | `401` 缺/错 Key；`404` Agent 名不存在；`422` payload 为空或超 30 字段；`503` 服务端未配 `WORKFLOW_API_KEY` |

`validation` 是给自动化流程的「可判定信号」：外部系统拿到结果就知道这条要不要转人工，不用自己重写校验规则。

---

## 2. 前置条件

1. **启动后端**（仓库根目录双击 `启动.bat`，或 `backend\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000`）。
2. **配置 Key**：在 `backend/.env` 加一行 `WORKFLOW_API_KEY=你自己设的一串随机值`（如 `WORKFLOW_API_KEY=sk-demo-xxxx`）。改完重启后端。
3. **网络可达**：
   - n8n / Coze 与后端在**同一台机器**：`http://localhost:8000`。
   - n8n 跑在 **Docker** 里（最常见）：容器内的 `localhost` 不是宿主机，URL 要用 `http://host.docker.internal:8000`（Windows / Mac  Docker Desktop 默认支持）。
   - Coze 云端工作流：必须填你**公网可访问**的地址（内网 `localhost` 云端访问不到），可用内网穿透临时暴露。

---

## 3. n8n（导入 `n8n-workflow-webhook.json`）

### 3.1 配置环境变量
n8n 里：`Settings → Env Variables`，新增两个（避免把密钥写进工作流文件）：
- `WORKBENCH_BASE_URL` = `http://host.docker.internal:8000`（或你的实际地址）
- `WORKFLOW_API_KEY` = 与 `backend/.env` 里一致的值

### 3.2 导入并运行
1. `Workflows → Import from File` → 选 `n8n-workflow-webhook.json`。
2. 流程：手动触发 → 示例商品(Code) → 调用 AI 工作台 Webhook(HTTP Request) → 结果摘要(Code)。
3. 点 `Execute Workflow`：
   - 示例商品节点生成一条 `{ payload: {...} }`（真实场景可换成 Google Sheet / Shopify 触发器）。
   - HTTP Request 节点 `POST {{$env.WORKBENCH_BASE_URL}}/api/hooks/agent/listing`，带 `X-API-Key` 头，body 为整个 item JSON。
   - 结果摘要节点把 `validation.status` 翻译成 `pass / warn / fail`，并打出 `need_human_review` 布尔——这就是接自动化分支的依据。

### 3.3 节点配置速查（导入异常时手动重建）
- **手动触发**：`n8n-nodes-base.manualTrigger`。
- **示例商品**（Code 节点，JS）：`return [{ json: { payload: { lang:'en', platform:'amazon', product:'Wireless Earbuds Pro', selling_points:'...', target_market:'United States' } } }];`
- **调用 AI 工作台 Webhook**（HTTP Request v4.2）：Method=`POST`，URL=`={{ $env.WORKBENCH_BASE_URL }}/api/hooks/agent/listing`，Send Headers 开，Headers=`X-API-Key = {{ $env.WORKFLOW_API_KEY }}`、`Content-Type = application/json`；Send Body 开，Body Type=`JSON`，JSON=`={{ $json }}`。
- **结果摘要**（Code 节点，JS）：把 `validation.status` 映射为 `pass/warn/fail`，输出 `need_human_review = (status==='fail')`。

### 3.4 进阶（真实批量）
把「示例商品」换成 `Google Sheets → Get Rows`，接 `Loop Over Items`（或 HTTP Request 自带批量），每行 product 自动调一次 Webhook，结果写回 Sheet——这就是简历里「CSV 批量任务 + 可嵌入自动化流程」的端到端闭环。

---

## 4. Coze（扣子）等价搭建（UI 内，无 JSON 导入）

Coze 工作流在可视化编辑器里搭，节点对应如下：

1. **开始** 节点：定义输入参数 `product / selling_points / lang / platform / target_market`。
2. **HTTP 请求** 节点（插件 → HTTP 请求）：
   - 方法：`POST`
   - URL：`https://你的公网地址/api/hooks/agent/listing`
   - Header：`X-API-Key` = 你配置的 Key；`Content-Type` = `application/json`
   - Body（JSON）：`{ "payload": { "lang": "{{lang}}", "platform": "{{platform}}", "product": "{{product}}", "selling_points": "{{selling_points}}", "target_market": "{{target_market}}" } }`
3. **代码** 节点（可选）：解析 `validation.status`，返回 `need_review = (status == 'fail')`。
4. **结束** 节点：输出 Agent 结果 + `validation`。

> Coze 云端无法访问你本机 `localhost`，必须给后端一个公网地址（临时用内网穿透即可），且 Key 通过 Coze 的「变量 / 密钥」功能注入，不要硬编码在 Body 里。

---

## 5. 先用 curl 验证契约（不依赖大模型）

后端起来、配好 `WORKFLOW_API_KEY` 后，在终端跑：

```bash
# 1) 缺 Key -> 期望 401
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8000/api/hooks/agent/listing \
  -H "Content-Type: application/json" -d '{"payload":{"product":"x"}}'

# 2) Key 正确但空 payload -> 期望 422
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8000/api/hooks/agent/listing \
  -H "X-API-Key: sk-demo-xxxx" -H "Content-Type: application/json" -d '{"payload":{}}'

# 3) 未知 Agent -> 期望 404
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8000/api/hooks/agent/not_exist \
  -H "X-API-Key: sk-demo-xxxx" -H "Content-Type: application/json" -d '{"payload":{"product":"x"}}'

# 4) 正常调用（需要大模型 Key 生效才会返回 Agent 结果；否则会 502）
curl -s -X POST http://localhost:8000/api/hooks/agent/listing \
  -H "X-API-Key: sk-demo-xxxx" -H "Content-Type: application/json" \
  -d '{"payload":{"lang":"en","platform":"amazon","product":"Wireless Earbuds Pro","selling_points":"anc, 40h","target_market":"US"}}'
```

前三条可以在**没有大模型额度**的情况下就验证整条 Webhook 链路（鉴权、路由、校验）是通的——面试演示时这几条状态码截图最稳。

---

## 6. 文件清单
- `examples/n8n-workflow-webhook.json` — 可直接导入 n8n 的工作流。
- `examples/webhook-mini-flow-guide.md` — 本说明。

> 这两个文件不含任何密钥（Key 走 n8n 环境变量 / Coze 密钥 / 后端 `.env`，均不入库），可随仓库提交作为「Webhook 已落地」的佐证。
