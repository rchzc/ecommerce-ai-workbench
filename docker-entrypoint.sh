#!/usr/bin/env bash
# ============================================================
# 降权启动入口：容器以 root 启动（Docker 默认），但对外提供 HTTP 服务的
# uvicorn 进程必须降权到非 root，缩小被攻破时的攻击面。
#
# 知识库向量索引落在持久卷 /app/backend/chroma_db，该挂载点默认属主是 root。
# 这里在 root 阶段先把它交给 appuser，再用 runuser 降权运行 uvicorn，
# 使进程全程非 root，同时仍对持久卷有写权限。
# （不在 Dockerfile 写 USER appuser，否则本入口脚本也会以 appuser 运行，
#  届时无法 chown 持久卷挂载点，ChromaDB 写入会失败。）
# ============================================================
set -euo pipefail

CHROMA_DIR="/app/backend/chroma_db"
mkdir -p "$CHROMA_DIR"
chown -R appuser:appuser "$CHROMA_DIR" 2>/dev/null || true

# 未传命令时使用默认启动参数
if [ "$#" -eq 0 ]; then
  set -- uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
fi

exec runuser -u appuser -- "$@"
