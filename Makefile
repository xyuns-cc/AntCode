# =============================================================================
# AntCode Makefile
# 统一的开发、测试、构建命令入口
# =============================================================================

.PHONY: help install sync lint format type-check test test-cov test-pbt \
        proto clean dev run-api run-master run-gateway run-worker \
        docker-up docker-down docker-build

# 默认目标
.DEFAULT_GOAL := help

# =============================================================================
# 帮助信息
# =============================================================================
help:
	@echo "AntCode 开发命令"
	@echo ""
	@echo "依赖管理:"
	@echo "  make install      - 安装所有依赖（包括开发依赖）"
	@echo "  make sync         - 同步 workspace 依赖"
	@echo ""
	@echo "代码质量:"
	@echo "  make lint         - 运行 ruff 检查"
	@echo "  make lint-fix     - 运行 ruff 检查并自动修复"
	@echo "  make format       - 格式化代码"
	@echo "  make type-check   - 运行 mypy 类型检查"
	@echo "  make check        - 运行所有检查（lint + type-check）"
	@echo ""
	@echo "测试:"
	@echo "  make test         - 运行所有测试"
	@echo "  make test-cov     - 运行测试并生成覆盖率报告"
	@echo "  make test-pbt     - 运行属性测试"
	@echo "  make test-unit    - 运行单元测试"
	@echo "  make test-int     - 运行集成测试"
	@echo ""
	@echo "Proto 生成:"
	@echo "  make proto        - 生成 gRPC 代码"
	@echo ""
	@echo "服务运行:"
	@echo "  make run-api      - 启动 Web API 服务"
	@echo "  make run-master   - 启动 Master 调度服务"
	@echo "  make run-gateway  - 启动 Gateway 网关服务"
	@echo "  make run-worker   - 启动 Worker 执行器"
	@echo "  make dev          - 启动开发模式（API + 热重载）"
	@echo ""
	@echo "Docker:"
	@echo "  make docker-up    - 启动 Docker 容器"
	@echo "  make docker-down  - 停止 Docker 容器"
	@echo "  make docker-build - 构建 Docker 镜像"
	@echo ""
	@echo "清理:"
	@echo "  make clean        - 清理缓存和临时文件"

# =============================================================================
# 依赖管理
# =============================================================================
install:
	uv sync --all-packages

sync:
	uv sync

# =============================================================================
# 代码质量
# =============================================================================
lint:
	uv run ruff check .

lint-fix:
	uv run ruff check --fix .

format:
	uv run ruff format .

type-check:
	uv run mypy packages services --ignore-missing-imports

check: lint type-check

# =============================================================================
# 测试
# =============================================================================
test:
	uv run pytest

test-cov:
	uv run pytest --cov --cov-report=html --cov-report=term-missing

test-pbt:
	uv run pytest -m pbt -v

test-unit:
	uv run pytest -m "not integration and not e2e" -v

test-int:
	uv run pytest -m integration -v

# =============================================================================
# Proto 生成
# =============================================================================
proto:
	@echo "生成 gRPC 代码..."
	@if [ -f scripts/gen_proto.sh ]; then \
		bash scripts/gen_proto.sh; \
	else \
		echo "Proto 生成脚本尚未创建，请先完成 Task 2.3"; \
	fi

# =============================================================================
# 服务运行
# =============================================================================
dev:
	uv run python -m antcode_web_api

run-api:
	@echo "启动 Web API 服务..."
	@uv run python -m antcode_web_api

run-master:
	@echo "启动 Master 调度服务..."
	@uv run python -m antcode_master

run-gateway:
	@echo "启动 Gateway 网关服务..."
	@uv run python -m antcode_gateway

run-worker:
	@echo "启动 Worker 执行器..."
	@uv run python -m antcode_worker

# =============================================================================
# Docker
# =============================================================================
docker-up:
	cd infra/docker && docker compose up -d

docker-down:
	cd infra/docker && docker compose down

docker-build:
	@echo "构建 Docker 镜像..."
	@cd infra/docker && docker compose build

# =============================================================================
# 清理
# =============================================================================
clean:
	@echo "清理缓存和临时文件..."
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "htmlcov" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name ".coverage" -delete 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@echo "清理完成"
