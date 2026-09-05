# 容器加固与 SSE 集成测试 · 交付说明

> 承接上一轮「代码质量评估与修复」的遗留加固项。本轮补齐生产交付门槛：密钥不进镜像、进程非 root、存活探针、以及流式接口的集成测试。

## 1. 安全修复：密钥不再打进镜像（P0 隐患）

发现仓库存在 `backend/.env`（含真实 `LLM_API_KEY`），而原先 **没有 `.dockerignore`**，`Dockerfile` 的 `COPY backend/ ./` 会把该文件固化进镜像层，任何人拿到镜像都能提取密钥。

- 新增仓库根 **`.dockerignore`**：排除 `**/.env`、`**/.env.*`（保留 `.env.example` 占位）、`.git`、`node_modules`、`__pycache__`、`*.pyc`、`chroma_db`、`*.sqlite3`、`backend/tests`。
- 双重收益：既堵住密钥泄露，又把构建上下文从「整仓」降到「必要文件」，构建更快。

## 2. 容器加固：非 root 降权 + HEALTHCHECK

| 改动 | 文件 | 说明 |
| --- | --- | --- |
| 非 root 用户 | `Dockerfile` | 运行时阶段 `useradd --uid 1000 appuser` |
| 降权入口 | `docker-entrypoint.sh`（新增） | root 阶段先把持久卷 `/app/backend/chroma_db` `chown` 给 `appuser`，再用 `runuser` 降权运行 uvicorn |
| 存活探针 | `Dockerfile` + `docker-compose.yml` | `HEALTHCHECK` 复用 `/api/ready`（配置错误仍返回 200 `ready=false`，适合存活探测；区别于 `/api/health` 的 503） |
| 最小权限 | `docker-compose.yml` | `security_opt: no-new-privileges:true` |

**为什么不在 Dockerfile 写 `USER appuser`**：入口脚本本身也需要以 root 运行才能 `chown` 持久卷挂载点；降权只作用在最终的 uvicorn 进程上（通过 `runuser`）。Compose 里也**不设置 `user:`**，否则入口脚本会以该用户运行，同样无法完成 chown。

## 3. SSE 流式接口集成测试（新增 6 例）

文件：`backend/tests/test_sse_streaming.py`。不依赖真实 LLM，通过 `app.dependency_overrides[deps.get_agent_service]` 注入脚本化服务，用 `FastAPI TestClient` 验证：

1. **事件顺序严格为** `meta → knowledge → delta* → done`（前端打字机效果的数据正确性）。
2. `meta` 携带模型路由结果（`model/tier/route_score`），`knowledge` 含资料来源（成本看板 + 引用来源的数据来源）。
3. 多段 `delta` 文本拼接后等于完整输出。
4. 业务异常（`AppError`）/ 未预期异常都被收敛为 `type=error` 事件，且**流不会中断在半路**（之前担心流式异常导致前端卡死，这里固化了行为）。
5. `/api/ready` 探针始终返回 200（HEALTHCHECK 的依赖）。

### 实现中的关键坑（已记录，避免复踩）
- override 写成 `lambda request:` 会被 FastAPI 当成 **query 参数** → 改为无参 `lambda:`。
- 失败桩必须是 **async generator**（含 `yield`），否则 `async for` 报 `TypeError` 而非原异常，丢失错误语义。
- `payload` 空字典会触发 schema 校验 422（「payload 不能为空」）→ 测试须传非空 payload。

## 4. 验证结论

- 后端 pytest：**77 passed**（原有 71 + 本轮新增 6）。
- `bash -n docker-entrypoint.sh`：语法 OK。
- ⚠️ 本机环境**未安装 docker**，未实跑 `docker build` / `docker compose up`；建议在有 docker 的机器上执行一次 `docker compose build && docker compose up`，确认：容器以 `appuser` 启动（`ps` 可见非 root）、`/api/ready` 探针被 healthcheck 判定 healthy、持久卷可写。

## 5. 交付文件清单

- `.dockerignore`（新增）
- `docker-entrypoint.sh`（新增）
- `Dockerfile`（运行时阶段加固）
- `docker-compose.yml`（healthcheck + no-new-privileges）
- `backend/tests/test_sse_streaming.py`（新增 6 例集成测试）
