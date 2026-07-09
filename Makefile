# =============================================================================
# AntCode Makefile
# 统一的开发、测试、构建命令入口
# =============================================================================

.PHONY: help install sync lint lint-fix format type-check check \
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
	@echo "  make type-check   - mypy 类型检查"
	@echo "  make check        - lint + type-check"
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
	@echo "  make docker-up    - docker compose up -d"
	@echo "  make docker-down  - docker compose down"
	@echo "  make docker-build - 单架构构建"
	@echo "  make docker-buildx- amd64 + arm64 多架构构建"
	@echo ""
	@echo "清理:"
	@echo "  make clean        - 清 pycache / ruff cache / coverage 等"

# =============================================================================
# 依赖管理
# =============================================================================
install:
	uv sync --all-packages

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

test-unit:
	uv run pytest -m "not integration and not e2e" -v

test-int:
	uv run pytest -m integration -v

# =============================================================================
# Proto 生成
# =============================================================================
proto:
	@echo "生成 gRPC 代码..."
	@bash scripts/gen_proto.sh

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

# 构建多架构镜像（amd64 + arm64），需事先 `docker buildx create --use`
# 用于跨平台部署（x86 服务器 + Apple Silicon / 国产鲲鹏 arm64）
docker-buildx:
	@echo "多架构构建 amd64+arm64（推送到本地 daemon 仅支持单架构，用 --push 上传 registry 才能保留全部）..."
	@for svc in web_api master gateway worker; do \
		docker buildx build --platform linux/amd64,linux/arm64 \
			-f infra/docker/Dockerfile.$$svc \
			-t antcode-$$svc:multiarch . || exit 1; \
	done
	@echo "多架构构建完成。生产推送请 append --push --tag your-registry/antcode-xxx:tag"

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
