# ============================================================
# 单容器交付：Node 仅用于构建前端，运行时只有 Python 镜像统一托管
# 前端构建产物由 FastAPI 直接提供（main.py 已挂载 /assets 和 /）
# ============================================================

# ---------- 阶段 1：构建前端 ----------
FROM node:22-alpine AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --registry https://registry.npmmirror.com
COPY frontend/ ./
RUN npm run build

# ---------- 阶段 2：Python 运行时 ----------
FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# 创建非 root 运行用户：对外提供 HTTP 服务的进程不以 root 运行，
# 降低容器被攻破时的横向移动与提权风险。
RUN useradd --uid 1000 --create-home --shell /bin/bash appuser

WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

COPY backend/ ./
# 前端构建产物放到 main.py 约定的 /app/frontend/dist
COPY --from=frontend /app/frontend/dist /app/frontend/dist

# 知识库向量索引落在持久卷，避免重建容器后丢失
VOLUME ["/app/backend/chroma_db"]

# 降权启动：入口脚本在 root 阶段把持久卷交给 appuser，再用 runuser 降权运行 uvicorn。
# （不在 Dockerfile 写 USER appuser，否则入口脚本本身也以 appuser 运行，无法 chown 挂载点。）
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

EXPOSE 8000
# 就绪探针始终返回 200（ready=false 仅表示配置待补），适合编排系统轮询存活状态，
# 因此 HEALTHCHECK 用 /api/ready 而非 /api/health（后者配置错误时返回 503）。
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/ready').status==200 else 1)"

# 启动时不会自动重建知识库（需要 API Key）；首次部署后调一次 POST /api/kb/rebuild
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
