"""应用层配置 = 数据中台配置 + Web 交付层自己的字段。

**为什么这个文件只有一百多行**：数据中台被抽成独立包
（`packages/data_platform`）之后，它已经拥有了数据目录、飞书凭证、批量上限、
流水线调度这些配置项。应用层如果自己再定义一遍，同一份事实就有两个来源 ——
改一处忘一处就会出现"配置里禁用了通配符、中间件却还在用 *"这类问题
（本项目真踩过，见下面 `load_cors_origins` 的注释）。

所以这里只做三件事：
1. 继承 `data_platform.Settings`，**只追加** Web 层自己的字段；
2. 把路径类字段的默认值落到本仓 `backend/` 下（包的默认值指向包自己的 `data/`）；
3. 在包外补一次 `.env` 加载 —— 应用读的是 `backend/.env`，不是包的查找规则。
"""
from __future__ import annotations

import dataclasses
import os

from dotenv import load_dotenv

# 厂商 preset 与复杂度路由关键词的唯一来源是共享包，这里只做对外 re-export，
# 保持历史 import 路径（`from ..config import LIGHT_HINTS`）继续可用。
from ecom_shared.config import (  # noqa: F401
    COMPLEX_LENGTH_THRESHOLD,
    HEAVY_HINTS,
    LIGHT_HINTS,
    PROVIDER_PRESETS,
)
from data_platform.config import Settings as DataPlatformSettings
from data_platform.config import load_settings as _load_data_platform_settings
from data_platform.config import resolve_paths

# 复用 errors.py 的类型化错误，避免同名类在两个模块各定义一份。
# 之前 config.ConfigError 继承 Exception、errors.ConfigError 继承 AppError，
# 两者同名不同类：捕获其中一个时另一个会漏掉，全局错误处理器也就失效了。
from .errors import ConfigError  # noqa: F401  (对外 re-export，保持历史 import 路径可用)

# 在读取环境变量之前加载 .env（位于 backend/.env），避免"没填配置却不报错"。
# 用绝对路径，避免依赖启动时的当前工作目录。
_ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
load_dotenv(_ENV_PATH)

# 目录约定（绝对路径，不依赖启动时的工作目录）：
#   _THIS_DIR   = backend/app
#   BACKEND_DIR = backend
#   PROJECT_DIR = 仓库根
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(_THIS_DIR)
PROJECT_DIR = os.path.dirname(BACKEND_DIR)


# ---------------------------------------------------------------------------
# 运行期路径与全局开关
#
# 单独暴露成函数，是因为这些值在「配置校验失败」时也必须可读：
# main.py 创建应用时就要挂 CORS 中间件、算静态目录，而此时 Settings 还不存在。
# 以前 main.py 直接写 os.getenv，导致同一份配置在 config.py 和 main.py 各解析一遍，
# 两处规则不一致就会出现"配置里禁用了通配符、中间件却还在用 *"。
# ---------------------------------------------------------------------------
def default_chroma_dir() -> str:
    return os.path.join(BACKEND_DIR, "chroma_db")


def default_static_dir() -> str:
    return os.path.join(PROJECT_DIR, "frontend", "dist")


def resolve_chroma_dir() -> str:
    return os.getenv("CHROMA_DIR") or default_chroma_dir()


def resolve_static_dir() -> str:
    return os.getenv("STATIC_DIR") or default_static_dir()


def load_log_level() -> str:
    return (os.getenv("LOG_LEVEL") or "INFO").strip().upper()


def load_cors_origins() -> list[str]:
    """解析 CORS 白名单。

    生产环境若仍是 "*"，只保留通配并在日志里留痕 —— 显式配置优先，
    未配置时才退化到通配（本地开发场景），并统一由这里决定，避免多处不一致。
    """
    raw = os.getenv("CORS_ORIGINS", "*")
    origins = [o.strip() for o in raw.split(",") if o.strip()]
    return origins or ["*"]


@dataclasses.dataclass(frozen=True)
class Settings(DataPlatformSettings):
    """数据中台配置 + Web 交付层字段。

    有默认值的字段放最后，便于测试只构造关心的那几个。
    """

    # 静态资源目录（前端构建产物），由 FastAPI 直接托管 —— 单容器交付的基础
    static_dir: str = ""
    # 向量索引持久化目录
    chroma_dir: str = ""
    # 对外 Webhook 的调用凭据：不配则 Webhook 端点直接 503，不做静默放行
    workflow_api_key: str = ""
    # CORS 白名单；默认通配只适用于本地开发
    cors_origins: list[str] = dataclasses.field(default_factory=lambda: ["*"])


def load_settings() -> Settings:
    """读取并校验配置。任何一项不合法都直接抛 ConfigError。

    共享层与数据中台层的校验全部交给 `data_platform.load_settings()` ——
    本函数只做两件事：把路径默认值指向本仓 `backend/`，再补上 Web 层字段。

    `load_env_file=False`：本模块导入时已经 `load_dotenv(_ENV_PATH)` 过一次，
    这里不再重复读文件。重复读的后果是 `load_settings()` 变成"会改写进程环境"的
    函数 —— 测试里 `monkeypatch.delenv("LLM_API_KEY")` 删掉的变量会被 .env 填回来，
    于是"缺 Key 应当抛 ConfigError"的用例静默失效（本项目真踩过）。
    """
    base = _load_data_platform_settings(_ENV_PATH, load_env_file=False)

    # 路径默认落在本仓 backend/ 下（包的默认值指向包自己的 data/）。
    # 显式设了 DATA_DIR 就听环境变量的 —— 容器里挂的是卷。
    paths = resolve_paths(os.getenv("DATA_DIR") or os.path.join(BACKEND_DIR, "data"))

    # 继承来的字段先铺一层，再用本层覆盖。
    # 注意不能写成 Settings(**基类字段, **paths)：paths 里也有 data_dir 等同名键，
    # 展开成关键字参数时会直接 TypeError: got multiple values for keyword argument。
    values = {f.name: getattr(base, f.name) for f in dataclasses.fields(base)}
    values.update(paths)
    values.update(
        # 向量目录跟着应用走，不用共享包的默认（那是共享包仓库的位置）
        vector_dir=os.getenv("VECTOR_DIR") or resolve_chroma_dir(),
        static_dir=resolve_static_dir(),
        chroma_dir=resolve_chroma_dir(),
        workflow_api_key=os.getenv("WORKFLOW_API_KEY", "").strip(),
        cors_origins=load_cors_origins(),
    )
    return Settings(**values)
