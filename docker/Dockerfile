# =====================================================
# AntCode 多阶段构建 Dockerfile
# 支持后端（FastAPI）和前端（React）分别打包
# 通过 --target backend-runtime / frontend-runtime 选择输出镜像
# =====================================================

# ================== 后端: 基础镜像 ==================
FROM python:3.11-slim AS backend-base

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_SYSTEM_PYTHON=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.cargo/bin:${PATH}"

# ================== 后端: 依赖安装 ==================
FROM backend-base AS backend-builder

WORKDIR /app

COPY pyproject.toml uv.lock* ./

ARG DB_TYPE=sqlite
RUN if [ "$DB_TYPE" = "mysql" ]; then \
        echo "📦 安装 MySQL 依赖..." && \
        uv sync --extra mysql; \
    elif [ "$DB_TYPE" = "postgres" ]; then \
        echo "📦 安装 PostgreSQL 依赖..." && \
        uv sync --extra postgres; \
    elif [ "$DB_TYPE" = "all" ]; then \
        echo "📦 安装所有数据库依赖..." && \
        uv sync --extra all-db; \
    else \
        echo "📦 安装基础依赖（SQLite）..." && \
        uv sync; \
    fi

# ================== 后端: 运行时 ==================
FROM backend-base AS backend-runtime

WORKDIR /app

COPY --from=backend-builder /root/.local /root/.local
COPY --from=backend-builder /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH"

COPY src ./src
COPY main.py ./

RUN mkdir -p \
    /app/storage/projects/files \
    /app/storage/projects/extracted \
    /app/storage/projects/executions \
    /app/storage/venvs/shared \
    /app/storage/mise/cache \
    /app/logs/tasks \
    /app/migrations

RUN chmod -R 755 /app/storage /app/logs

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/health || exit 1

CMD ["python", "main.py"]

# ================== 前端: 构建 ==================
FROM node:20-alpine AS frontend-builder

WORKDIR /frontend

ENV NODE_ENV=production \
    HUSKY=0

COPY web/antcode-frontend/package*.json ./
RUN npm ci --legacy-peer-deps

COPY web/antcode-frontend ./

ARG VITE_API_BASE_URL=http://localhost:8000
ARG VITE_APP_TITLE="AntCode 任务调度平台"

ENV VITE_API_BASE_URL=$VITE_API_BASE_URL \
    VITE_APP_TITLE=$VITE_APP_TITLE

RUN npm run build

# ================== 前端: 运行时 ==================
FROM nginx:1.27-alpine AS frontend-runtime

RUN apk add --no-cache curl

COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=frontend-builder /frontend/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://127.0.0.1/healthz || exit 1

CMD ["nginx", "-g", "daemon off;"]
