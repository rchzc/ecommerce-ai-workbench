"""核心基础设施：LLM / 向量化 / 向量库 / 切分 / 重排。

注意：本 __init__ 只 re-export 无重依赖的模块（rag / rerank）。
embeddings / vectorstore / llm 会拉起 openai / chromadb 等第三方依赖，
改由各子模块按需导入，避免「只要 import 任意 core 子模块就触发重依赖」，
从而让离线评测脚本在缺少第三方包的环境下也能直接复用 rag / rerank。
"""
from .rag import Chunk, chunk_document, load_documents  # noqa: F401
from .rerank import rerank  # noqa: F401
