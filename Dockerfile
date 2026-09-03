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

WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

COPY backend/ ./
# 前端构建产物放到 main.py 约定的 /app/frontend/dist
COPY --from=frontend /app/frontend/dist /app/frontend/dist

# 知识库向量索引落在持久卷，避免重建容器后丢失
VOLUME ["/app/backend/chroma_db"]

EXPOSE 8000
# 启动时不会自动重建知识库（需要 API Key）；首次部署后调一次 POST /api/kb/rebuild
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1"]
