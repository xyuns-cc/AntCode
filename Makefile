# =============================================================================
# AntCode Makefile
# 统一的开发、测试、构建命令入口
# =============================================================================

.PHONY: help install sync lint lint-fix format complexity complexity-baseline-update type-check check \
        test test-cov test-unit test-int \
        proto clean init-db \
        dev-api dev-master dev-worker dev-gateway dev-web \
        docker-up docker-down docker-build docker-buildx

# 默认目标
.DEFAULT_GOAL := help

# =============================================================================
# 帮助信息
# =============================================================================
help:
	@echo "AntCode 开发命令"
	@echo ""
	@echo "依赖管理:"
	@echo "  make install      - 安装 python + workspace 全部子包"
	@echo "  make sync         - 只装 root 依赖（快速）"
	@echo ""
	@echo "初始化:"
	@echo "  make init-db      - 一键建 DB 表 + 索引 + 默认管理员"
	@echo ""
	@echo "本地开发:"
	@echo "  make dev-api      - 启动 web_api"
	@echo "  make dev-master   - 启动 master"
	@echo "  make dev-worker   - 启动 worker（direct 模式）"
	@echo "  make dev-gateway  - 启动 gateway（跨网络场景才需要）"
	@echo "  make dev-web      - 启动前端 (vite dev server)"
	@echo ""
	@echo "代码质量:"
	@echo "  make lint         - ruff 检查"
	@echo "  make lint-fix     - ruff 自动修复"
	@echo "  make format       - ruff 格式化"
	@echo "  make complexity   - 严格复杂度增量门禁"
	@echo "  make complexity-baseline-update - 仅收紧复杂度基线"
	@echo "  make type-check   - mypy 类型检查"
	@echo "  make check        - lint + complexity + type-check"
	@echo ""
	@echo "测试:"
	@echo "  make test         - 全量 pytest"
	@echo "  make test-cov     - 带覆盖率报告"
	@echo "  make test-unit    - 只跑单元"
	@echo "  make test-int     - 只跑集成"
	@echo ""
	@echo "Proto:"
	@echo "  make proto        - 重新生成 gRPC pb2"
	@echo ""
	@echo "Docker:"
	@echo "  make docker-up    - 启动 dev Compose"
	@echo "  make docker-down  - 停止 dev Compose"
	@echo "  make docker-build - 构建 dev Compose 镜像"
	@echo "  make docker-buildx - amd64 + arm64 多架构本地 OCI 构建"
	@echo ""
	@echo "清理:"
	@echo "  make clean        - 清 pycache / ruff cache / coverage 等"

# =============================================================================
# 依赖管理
# =============================================================================
install:
	uv sync --all-packages --extra dev

sync:
	uv sync

# =============================================================================
# 初始化
# =============================================================================
init-db:
	@echo "初始化数据库（建表 + 补索引 + 建默认管理员）..."
	@uv run python scripts/init_db.py

# =============================================================================
# 本地开发
# =============================================================================
dev-api:
	@echo "启动 web_api (http://localhost:8000)..."
	@uv run uvicorn antcode_web_api.app:app --host 0.0.0.0 --port 8000 --reload

dev-master:
	@echo "启动 master..."
	@uv run python -m antcode_master

dev-worker:
	@echo "启动 worker (direct 模式，需要先跑 web_api)..."
	@uv run python -m antcode_worker run --name dev-worker-1 --transport direct

dev-gateway:
	@echo "启动 gateway..."
	@uv run python -m antcode_gateway

dev-web:
	@echo "启动前端 (http://localhost:3000)..."
	@cd web/antcode-frontend && npm run dev

# =============================================================================
# 代码质量
# =============================================================================
lint:
	uv run ruff check .

lint-fix:
	uv run ruff check --fix .

format:
	uv run ruff format .

complexity:
	uv run python -m scripts.check_complexity

complexity-baseline-update:
	uv run python -m scripts.check_complexity --update-baseline

type-check:
	uv run mypy packages services --ignore-missing-imports

check: lint complexity type-check

# =============================================================================
# 测试
# =============================================================================
test:
	uv run pytest

test-cov:
	uv run pytest --cov --cov-report=html --cov-report=term-missing

test-unit:
	uv run pytest tests/unit -v

test-int:
	@: "$${ANTCODE_INTEGRATION_REDIS_URL:?ANTCODE_INTEGRATION_REDIS_URL must be set}"
	@: "$${DATABASE_URL:?DATABASE_URL must be set}"
	@: "$${TEST_DATABASE_URL:?TEST_DATABASE_URL must be set}"
	uv run pytest tests/integration -v

# =============================================================================
# Proto 生成
# =============================================================================
proto:
	@echo "生成 gRPC 代码..."
	@uv run python scripts/generate_proto.py

# =============================================================================
# Docker
# =============================================================================
docker-up:
	docker compose -f infra/docker/docker-compose.dev.yml up -d

docker-down:
	docker compose -f infra/docker/docker-compose.dev.yml down

docker-build:
	@echo "构建 Docker 镜像..."
	@docker compose -f infra/docker/docker-compose.dev.yml build

# 构建本地多架构 OCI 归档。正式发布只能由受保护的 CI 工作流执行。
docker-buildx:
	@if [ -n "$${BUILDX_REGISTRY:-}" ] || [ -n "$${BUILDX_TAG:-}" ]; then \
		echo "docker-buildx does not publish; remove BUILDX_REGISTRY/BUILDX_TAG and use the verified CI release workflow"; \
		exit 2; \
	fi
	@echo "构建本地多架构 OCI 归档；正式发布仅允许 .github/workflows/ci.yml"
	@output_dir="$${BUILDX_OUTPUT_DIR:-build/docker}"; \
		mkdir -p "$$output_dir"; \
		for spec in \
		"web-api|.|infra/docker/Dockerfile.web_api" \
		"master|.|infra/docker/Dockerfile.master" \
		"gateway|.|infra/docker/Dockerfile.gateway" \
		"worker|.|infra/docker/Dockerfile.worker" \
		"frontend|web/antcode-frontend|web/antcode-frontend/Dockerfile"; do \
		svc="$${spec%%|*}"; rest="$${spec#*|}"; \
		context="$${rest%%|*}"; dockerfile="$${rest#*|}"; \
		docker buildx build --platform linux/amd64,linux/arm64 \
			-f "$$dockerfile" \
			-t "antcode-$$svc:local" \
			--output "type=oci,dest=$$output_dir/antcode-$$svc.oci.tar" \
			"$$context" || exit 1; \
	done
	@echo "本地多架构 OCI 归档构建完成：$${BUILDX_OUTPUT_DIR:-build/docker}"

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
