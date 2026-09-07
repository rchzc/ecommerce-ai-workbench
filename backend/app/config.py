"""集中配置：全部来自环境变量，启动时校验，缺失即快速失败。

设计要点（面试可讲）：
1. 所有配置集中在一处，业务代码不直接读 os.getenv，避免配置散落。
2. 启动时 validate() 一次性校验，缺密钥直接抛错，不做静默降级 —— 静默降级会让
   "调不通"伪装成"跑通了"，是 Demo 项目最常见的坑。
3. 多厂商 preset 表：切换 provider 只改 .env，业务代码零改动。
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# 复用 errors.py 的类型化错误，避免同名类在两个模块各定义一份。
# 之前 config.ConfigError 继承 Exception、errors.ConfigError 继承 AppError，
# 两者同名不同类：捕获其中一个时另一个会漏掉，全局错误处理器也就失效了。
from .errors import ConfigError  # noqa: F401  (对外 re-export，保持历史 import 路径可用)

# 在读取环境变量之前加载 .env（位于 backend/.env），避免"没填配置却不报错"
# 用绝对路径，避免依赖启动时的当前工作目录
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
# 之前 main.py 直接写 os.getenv，导致同一份配置在 config.py 和 main.py 各解析一遍，
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


# ---------------------------------------------------------------------------
# 多厂商 preset：统一走 OpenAI 兼容协议
# ---------------------------------------------------------------------------
PROVIDER_PRESETS: dict[str, dict[str, str]] = {
    "dashscope": {
        "label": "阿里云百炼",
        "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "light": "qwen-plus",
        "heavy": "qwen-max",
        "embedding": "text-embedding-v3",
        "supports_embedding": "true",
    },
    "deepseek": {
        "label": "DeepSeek",
        "api_base": "https://api.deepseek.com/v1",
        "light": "deepseek-chat",
        "heavy": "deepseek-chat",
        "embedding": "",
        "supports_embedding": "false",
    },
    "openai": {
        "label": "OpenAI",
        "api_base": "https://api.openai.com/v1",
        "light": "gpt-4o-mini",
        "heavy": "gpt-4o",
        "embedding": "text-embedding-3-small",
        "supports_embedding": "true",
    },
    "ollama": {
        "label": "Ollama 本地",
        "api_base": "http://localhost:11434/v1",
        "light": "qwen2.5:3b",
        "heavy": "qwen2.5:7b",
        "embedding": "nomic-embed-text",
        "supports_embedding": "true",
    },
}

# 轻量任务关键词：命中越多越倾向轻量模型
LIGHT_HINTS = ("分类", "判断", "提取", "是否", "翻译", "总结", "归类", "标签")
# 重量任务关键词：命中越多越倾向重量模型
HEAVY_HINTS = ("分析", "诊断", "生成", "策略", "优化", "撰写", "方案", "评估")
# 文本超过该长度直接判定为复杂任务
COMPLEX_LENGTH_THRESHOLD = 200


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


@dataclass(frozen=True)
class Settings:
    """运行时配置快照（不可变，避免运行期被意外修改）。"""

    provider: str
    api_key: str
    api_base: str
    model_light: str
    model_heavy: str
    model_embedding: str
    supports_embedding: bool
    top_k: int
    chunk_size: int
    chunk_overlap: int
    request_timeout: int
    cors_origins: list[str]
    log_level: str
    chroma_dir: str
    static_dir: str
    # --- 批量任务与对外 Webhook ---
    # 对外 Webhook 的调用凭据：不配则 Webhook 端点直接 503，不做静默放行
    workflow_api_key: str
    # 单个批量任务的行数上限：防止一次提交几千行把模型额度打满
    batch_max_rows: int
    # 批量任务的并发度：并发太高会触发厂商限流，太低则批量没有意义
    batch_concurrency: int

    @property
    def provider_label(self) -> str:
        return PROVIDER_PRESETS.get(self.provider, {}).get("label", self.provider)


def load_settings() -> Settings:
    """读取并校验配置。任何一项不合法都直接抛 ConfigError。"""
    provider = _env("LLM_PROVIDER", "dashscope").lower()
    if provider not in PROVIDER_PRESETS:
        raise ConfigError(
            f"未知的 LLM_PROVIDER={provider!r}，可选：{', '.join(PROVIDER_PRESETS)}"
        )

    preset = PROVIDER_PRESETS[provider]
    api_key = _env("LLM_API_KEY") or _env("API_KEY") or _env("DASHSCOPE_API_KEY")
    # 本地 Ollama 不需要 key
    if provider != "ollama" and not api_key:
        raise ConfigError(
            f"缺少 LLM_API_KEY。当前 provider={provider}（{preset['label']}），"
            f"请在 backend/.env 中填入对应厂商的 API Key 后重启服务。"
        )

    try:
        top_k = int(_env("TOP_K", "4"))
        chunk_size = int(_env("CHUNK_SIZE", "600"))
        chunk_overlap = int(_env("CHUNK_OVERLAP", "50"))
        timeout = int(_env("REQUEST_TIMEOUT", "120"))
    except ValueError as exc:
        raise ConfigError(f"数值型配置项解析失败：{exc}") from exc

    if chunk_overlap >= chunk_size:
        raise ConfigError("CHUNK_OVERLAP 必须小于 CHUNK_SIZE")

    if top_k < 1:
        raise ConfigError("TOP_K 必须 >= 1")
    if chunk_size < 1:
        raise ConfigError("CHUNK_SIZE 必须 >= 1")
    if timeout < 1:
        raise ConfigError("REQUEST_TIMEOUT 必须 >= 1")

    try:
        batch_max_rows = int(_env("BATCH_MAX_ROWS", "200"))
        batch_concurrency = int(_env("BATCH_CONCURRENCY", "3"))
    except ValueError as exc:
        raise ConfigError(f"批量任务配置项解析失败：{exc}") from exc

    if batch_max_rows < 1:
        raise ConfigError("BATCH_MAX_ROWS 必须 >= 1")
    if batch_concurrency < 1:
        raise ConfigError("BATCH_CONCURRENCY 必须 >= 1")

    # CORS / 路径 / 日志级别统一走上面那组函数，保证与 main.py 读到的完全一致
    return Settings(
        provider=provider,
        api_key=api_key,
        api_base=_env("LLM_API_BASE") or preset["api_base"],
        model_light=_env("MODEL_LIGHT") or preset["light"],
        model_heavy=_env("MODEL_HEAVY") or preset["heavy"],
        model_embedding=_env("MODEL_EMBEDDING") or preset["embedding"],
        supports_embedding=preset["supports_embedding"] == "true",
        top_k=top_k,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        request_timeout=timeout,
        cors_origins=load_cors_origins(),
        log_level=load_log_level(),
        chroma_dir=resolve_chroma_dir(),
        static_dir=resolve_static_dir(),
        workflow_api_key=_env("WORKFLOW_API_KEY"),
        batch_max_rows=batch_max_rows,
        batch_concurrency=batch_concurrency,
    )
