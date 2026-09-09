# 交付说明：补齐"飞书多维表格"缺口

依据 `面试准备_君睿诚_AI数字化应用工程师.md` 的结论（JD 首个标签"飞书多维表格"是最大缺口、唯一 3 天能补的），产出可执行的 demo 链路与操作手册。

## 新增文件

| 文件 | 作用 |
|---|---|
| `飞书多维表格_3小时补齐方案.md` | 主手册：字段设计表、两个公式、自动化步骤、Webhook 接线、截图清单、面试讲稿与追问应对 |
| `backend/app/agents/replenish.py` | 第 7 个 Agent（库存补货建议），输出字段直接对齐飞书表格列 |
| `examples/feishu-bitable/bitable_sync.py` | 飞书表格 ↔ 工作台同步脚本，零第三方依赖，支持 `--demo` / `--dry-run` / `--apply` |

## 修改文件

- `backend/app/agents/__init__.py`：注册表加 `replenish`（前端零改动自动出现）
- `backend/tests/test_agents.py`：注册表断言改为子集判断，新增 Agent 不必回改测试
- `README.md`：6 → 7 个智能体，补 replenish 说明（领域仍 6 个，replenish 与 logistics 共用文档）
- `examples/webhook-mini-flow-guide.md`：Webhook `{name}` 枚举加 replenish
- `面试准备_君睿诚_AI数字化应用工程师.md` / `.html`：缺口项改为"已搭 demo"，补产物路径与讲稿
- `简历优化版_李文宇.md`：技能栏 Workflow/自动化 加"飞书多维表格（字段与公式设计、视图与仪表盘、自动化触发、Open API 读写回写）"

## 验证结果

- `pytest tests -q`：95 项全部通过
- `/api/agents`：返回 7 个 Agent，含 `replenish`
- `POST /api/hooks/agent/replenish`：无 Key → 401；带 Key → 200，返回
  `urgency=建议补货 / suggested_qty=268 / target_stock_days=45` 及 `validation: pass`（真实调用 qwen-max，8.0s）
- 同步脚本纯逻辑：`days_left` 计算、除零兜底、幂等跳过、脏值转数字均通过

## 下一步（用户侧，约 3 小时）

按手册第 1-3 节：建表 + 造数据 + 自动化 + 跑脚本回写，产出 3 张截图。时间不足时保底为"建表 + 跑一次 `--demo`"。
