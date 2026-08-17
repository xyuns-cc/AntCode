# =============================================================================
# AntCode Makefile
# 统一的开发、测试、构建命令入口
# =============================================================================

.PHONY: help install sync lint lint-fix format format-check complexity complexity-baseline-update type-check check \
        test test-cov test-unit test-contracts test-int \
        proto proto-check clean init-db \
        audit-python audit-npm audit \
        web-type-check web-lint web-test web-build web-check \
        release-gate \
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
	@echo "  make format-check - ruff 格式化校验（不改文件）"
	@echo "  make complexity   - 严格复杂度增量门禁"
	@echo "  make complexity-baseline-update - 仅收紧复杂度基线"
	@echo "  make type-check   - mypy 类型检查"
	@echo "  make check        - lint + format-check + complexity + type-check"
	@echo ""
	@echo "测试:"
	@echo "  make test         - 无外部依赖的套件（unit + boundary，见 testpaths）"
	@echo "  make test-cov     - 带覆盖率报告"
	@echo "  make test-unit    - 只跑单元"
	@echo "  make test-contracts - 只跑传输契约（需真 Redis）"
	@echo "  make test-int     - 只跑集成（需真 PostgreSQL + Redis）"
	@echo ""
	@echo "安全扫描:"
	@echo "  make audit-python - bandit + pip-audit，HIGH/CRITICAL 阻断"
	@echo "  make audit-npm    - npm audit，未批准的 HIGH/CRITICAL 阻断"
	@echo "  make audit        - audit-python + audit-npm"
	@echo ""
	@echo "前端:"
	@echo "  make web-type-check / web-lint / web-test / web-build"
	@echo "  make web-check    - 前端四件套串跑"
	@echo ""
	@echo "发布:"
	@echo "  make release-gate - 提交/发布前必须全绿的完整本地门禁"
	@echo ""
	@echo "Proto:"
	@echo "  make proto        - 重新生成 gRPC pb2"
	@echo "  make proto-check  - 校验生成物与 .proto 同步（不接受脏 diff）"
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

format-check:
	uv run ruff format --check .

complexity:
	uv run python -m scripts.check_complexity

complexity-baseline-update:
	uv run python -m scripts.check_complexity --update-baseline

type-check:
	uv run mypy packages services --ignore-missing-imports

check: lint format-check complexity type-check

# =============================================================================
# 安全扫描
# =============================================================================
# 仓库没有自动化流水线，这些扫描只会在有人执行下面的 target 时跑到。
audit-python:
	@rm -f bandit-report.json pip-audit-report.json audit-requirements.txt
	uv run bandit -r packages/ services/ scripts/ \
		--severity-level high --confidence-level high \
		--exit-zero -f json -o bandit-report.json
	@test -s bandit-report.json
	uv run --extra dev python scripts/fail_on_high_vulns.py bandit-report.json --tool bandit
	@# --all-packages 导出整个 workspace 的依赖闭包；只导出根包会漏扫 workspace 成员依赖。
	uv export --locked --all-packages --extra dev --no-emit-workspace --no-hashes \
		--output-file audit-requirements.txt
	@set +e; \
		uv run --extra dev pip-audit --strict --no-deps --disable-pip \
			--requirement audit-requirements.txt \
			--format=json --output=pip-audit-report.json; \
		scan_status=$$?; \
		set -e; \
		test -s pip-audit-report.json; \
		if [ "$$scan_status" -gt 1 ]; then exit "$$scan_status"; fi
	uv run --extra dev python scripts/fail_on_high_vulns.py pip-audit-report.json --tool pip-audit

# registry 必须点名官方源：常见的国内镜像（registry.npmmirror.com 等）没有实现 audit 接口，
# 返回的报告缺 auditReportVersion，门禁只会报「malformed」而永远验不到真实漏洞。
# 换句话说，跟着本机 npm config 走就等于没跑这条门禁。
NPM_AUDIT_REGISTRY := https://registry.npmjs.org

audit-npm:
	@cd web/antcode-frontend && \
		npm audit --json --audit-level=high --registry=$(NPM_AUDIT_REGISTRY) > npm-audit-report.json || audit_status=$$?; \
		test -s npm-audit-report.json; \
		if [ "$${audit_status:-0}" -gt 1 ]; then exit "$${audit_status}"; fi
	node scripts/check_npm_audit.mjs web/antcode-frontend/npm-audit-report.json

audit: audit-python audit-npm

# =============================================================================
# 前端
# =============================================================================
web-type-check:
	@cd web/antcode-frontend && npm run type-check

web-lint:
	@cd web/antcode-frontend && npm run lint:ci

web-test:
	@cd web/antcode-frontend && npm run test:ci

web-build:
	@cd web/antcode-frontend && npm run build

web-check: web-type-check web-lint web-test web-build

# =============================================================================
# 发布门禁
# =============================================================================
# 提交与发布前的完整本地门禁：生成物同步、lint、格式、复杂度、类型、后端测试
# （tests/unit + tests/boundary）、Python 与 npm 依赖审计、前端四件套。
#
# 它**不覆盖**下面两类，二者都不是可选项，只是跑不在开发机上：
#   1. 需要真实中间件/容器的测试：make test-contracts / make test-int /
#      infra/docker/run-gateway-e2e.sh 与 tests/e2e 的 run_e2e.sh
#   2. 需要外部工具镜像的扫描：gitleaks / hadolint / trivy
# 完整清单与执行环境见 docs/release-runbook.md 第 0 节。
release-gate: proto-check check test audit web-check
	@echo "本地发布门禁全绿；需要中间件/容器的门禁另见 docs/release-runbook.md §0.3 与 §0.4"

# =============================================================================
# 测试
# =============================================================================
# 范围由 pyproject.toml 的 testpaths 单点定义（tests/unit + tests/boundary），
# 即「不依赖外部中间件即可跑完」的那部分。需要真实依赖的三套（contracts / integration /
# e2e）有各自的 target，不在 release-gate 里，必须另行在有中间件或容器的环境执行。
test:
	uv run pytest

test-cov:
	uv run pytest --cov --cov-report=html --cov-report=term-missing

test-unit:
	uv run pytest tests/unit -v

test-contracts:
	@: "$${ANTCODE_CONTRACT_REDIS_URL:?ANTCODE_CONTRACT_REDIS_URL must be set}"
	uv run pytest tests/contracts -v

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

# 生成物必须与 .proto 同步：重新生成后不允许出现 diff。
proto-check:
	@uv run python scripts/generate_proto.py
	@git diff --exit-code -- contracts/proto packages/antcode_contracts/src/antcode_contracts

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

# 构建本地多架构 OCI 归档。本仓库没有自动发布通道，推送镜像必须在受控发布机上人工执行。
docker-buildx:
	@if [ -n "$${BUILDX_REGISTRY:-}" ] || [ -n "$${BUILDX_TAG:-}" ]; then \
		echo "docker-buildx does not publish; remove BUILDX_REGISTRY/BUILDX_TAG and push from a controlled release host"; \
		exit 2; \
	fi
	@echo "构建本地多架构 OCI 归档；本 target 只产出 OCI tar，不做任何推送"
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
