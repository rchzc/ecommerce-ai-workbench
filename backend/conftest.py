"""测试环境准备。

两件事：

1. 把 `backend/` 加进模块搜索路径，保证 `import app` 可用。
2. **把所有测试钉在"离线 + 目录隔离"状态上。** 这一步是 session 级 autouse 的，
   不指望每个用例自觉 —— 只要有一个用例忘了设 `LLM_PROVIDER=mock`，它就会去打
   真厂商接口：结果是"本机能过、CI 挂掉、还顺带烧掉一点额度"，最后没人敢跑测试。
   同理，路径类环境变量一旦被外部残留值污染（比如本机 `.env` 里配了真实的
   `DATA_DIR`），用例就会写到真实数据目录里去。

为什么环境变量的清理要覆盖 `.env` 读进来的那些键：`app/config.py` 在**模块导入时**
就 `load_dotenv(backend/.env)` 过一次，所以本机真实的 Key 和 provider 此刻已经在
`os.environ` 里了。测试要的是"确定性地按我设置的环境变量跑"，因此先把这些键全部
摘掉，再回填测试用值；测试结束后原样还原，不影响同进程里的其它代码。
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

# 需要隔离的环境变量：凡是能影响"去哪读文件、调哪个厂商"的，都要覆盖到。
_ISOLATED_ENV_KEYS = (
    # 模型与检索
    "LLM_PROVIDER",
    "LLM_API_KEY",
    "API_KEY",
    "DASHSCOPE_API_KEY",
    "LLM_API_BASE",
    "TOP_K",
    "CHUNK_SIZE",
    "CHUNK_OVERLAP",
    "REQUEST_TIMEOUT",
    "SESSION_MAX_TURNS",
    "VECTOR_BACKEND",
    # 路径
    "DATA_DIR",
    "DOCS_DIR",
    "IMPORT_DIR",
    "JOBS_DIR",
    "REPORTS_DIR",
    "SALES_DATA_DIR",
    "STATE_PATH",
    "CHROMA_DIR",
    "STATIC_DIR",
    "VECTOR_DIR",
    # 交付层与外部系统
    "LOG_LEVEL",
    "CORS_ORIGINS",
    "WORKFLOW_API_KEY",
    "BATCH_MAX_ROWS",
    "BATCH_CONCURRENCY",
    "PIPELINE_ENABLED",
    "PIPELINE_SCHEDULE",
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "FEISHU_BITABLE_APP_TOKEN",
    "FEISHU_TABLE_ID",
)

# 测试用的临时根目录。所有路径型配置都落在它下面，用例之间互不干扰，
# 也不会碰到仓库里真实的 backend/data。
TEST_ROOT = os.path.join(tempfile.gettempdir(), "ecommerce-ai-workbench-tests")

_OVERRIDES = {
    "LLM_PROVIDER": "mock",  # 主开关：绝不出网
    "VECTOR_BACKEND": "lexical",  # 向量检索走词法实现，不需要 embedding 服务
    "LOG_LEVEL": "CRITICAL",  # 日志不要混进测试输出
    "DATA_DIR": TEST_ROOT,
    "CHROMA_DIR": os.path.join(TEST_ROOT, "chroma_db"),
    "STATIC_DIR": os.path.join(TEST_ROOT, "static"),
    "VECTOR_DIR": os.path.join(TEST_ROOT, "vectors"),
}


@pytest.fixture(scope="session", autouse=True)
def _offline_env():
    """钉死离线与目录隔离配置，跑测试不可能碰到真厂商、真数据目录。"""
    saved = {key: os.environ.get(key) for key in _ISOLATED_ENV_KEYS}
    for key in _ISOLATED_ENV_KEYS:
        os.environ.pop(key, None)
    os.environ.update(_OVERRIDES)
    yield
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
