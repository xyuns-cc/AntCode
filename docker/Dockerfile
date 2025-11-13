# =====================================================
# AntCode API - 多阶段构建 Dockerfile
# =====================================================

# ================== 阶段 1: 基础镜像 ==================
FROM python:3.11-slim as base

# 设置环境变量
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_SYSTEM_PYTHON=1

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 安装 uv（更快的 Python 包管理器）
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.cargo/bin:${PATH}"

# ================== 阶段 2: 依赖安装 ==================
FROM base as builder

WORKDIR /app

# 复制依赖文件
COPY pyproject.toml uv.lock* ./

# 根据构建参数安装依赖
# 支持通过 --build-arg DB_TYPE=mysql 来指定数据库类型
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

# ================== 阶段 3: 运行时镜像 ==================
FROM base as runtime

WORKDIR /app

# 从构建阶段复制已安装的依赖
COPY --from=builder /root/.local /root/.local
COPY --from=builder /app/.venv /app/.venv

# 设置虚拟环境路径
ENV PATH="/app/.venv/bin:$PATH"

# 复制应用代码
COPY src ./src
COPY main.py ./

# 创建必要的目录
RUN mkdir -p \
    /app/storage/projects/files \
    /app/storage/projects/extracted \
    /app/storage/projects/executions \
    /app/storage/venvs/shared \
    /app/storage/mise/cache \
    /app/logs/tasks \
    /app/migrations

# 设置目录权限
RUN chmod -R 755 /app/storage /app/logs

# 暴露端口
EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/health || exit 1

# 启动命令
CMD ["python", "main.py"]
