# AntCode Docker 开发部署

## 目标

使用 `docker-compose.dev.yml` 一键拉起本地联调环境，覆盖：

- `web_api`（Control Plane）
- `master`（Schedule Plane）
- `gateway`（Data Plane）
- `worker`（Execution Plane）
- `postgres` / `redis` / `frontend`

## 快速启动

```bash
cd infra/docker
cp .env.example .env
docker compose -f docker-compose.dev.yml up -d
```

## 常用命令

```bash
# 服务状态
docker compose -f docker-compose.dev.yml ps

# 查看全部日志
docker compose -f docker-compose.dev.yml logs -f

# 查看单服务日志
docker compose -f docker-compose.dev.yml logs -f web-api

# 重建
docker compose -f docker-compose.dev.yml build --no-cache
docker compose -f docker-compose.dev.yml up -d --force-recreate
```

## 访问地址

- Frontend: `http://localhost:3000`
- Web API: `http://localhost:8000`
- Swagger: `http://localhost:8000/docs`
- Gateway gRPC: `localhost:50051`

## 数据目录规范（最新版）

容器内统一使用 `/app/data`，并严格分层：

```text
/app/data/
├── backend/          # web_api / master / gateway
│   ├── keys/
│   ├── work/
│   └── temp/
└── worker/           # worker
    ├── runs/
    ├── runtimes/
    ├── temp/
    ├── secrets/
    └── identity/
```

## Volume 说明

| Volume | 用途 |
|---|---|
| `postgres_data` | PostgreSQL 数据 |
| `redis_data` | Redis 数据 |
| `backend_data` | Web API 与 Master 共享的 `/app/data`，避免宿主目录 UID/GID 不匹配 |
| `worker_data` | 挂载到 `/app/data` 的运行时数据 |

## 关键环境变量

| 变量 | 说明 |
|---|---|
| `WEB_API_PORT` | Web API 端口 |
| `GATEWAY_GRPC_PORT` / `GATEWAY_PORT` | 本地 / 远程 Gateway gRPC 端口 |
| `FRONTEND_PORT` | 前端端口 |
| `POSTGRES_*` | PostgreSQL 账号与库配置 |
| `REDIS_PASSWORD` | Redis 密码 |
| `WORKER_TRANSPORT_MODE` / `WORKER_NAME` | Worker 基础配置 |
| `ANTCODE_TRUSTED_PROXIES` | Web API 直接上游反向代理的 IP/CIDR，逗号分隔；默认留空 |

## 反向代理客户端 IP

Web API 默认不信任 `X-Forwarded-For` 和 `X-Real-IP`，而是使用 socket 对端
地址。通过 Nginx、Ingress 或负载均衡器接入时，必须把**与 Web API 直接建立
连接的实际代理** IP/CIDR 配置到 `ANTCODE_TRUSTED_PROXIES`，多个值用逗号分隔。

不要填写 `0.0.0.0/0`、`::/0`、客户端网段或无法确认归属的共享网段。宽泛信任
会允许外部请求伪造来源 IP，破坏登录限流、审计记录和 Worker 安装来源绑定。
无法固定代理地址时应先固定部署网络或 Ingress 出口，再配置该变量；不要为了让
转发头生效而放宽信任范围。

## Worker 沙箱要求

Worker 使用 bubblewrap 隔离每个用户任务。Docker 默认策略会阻止嵌套
user/mount/pid namespace，表现为 `bwrap: No permissions to create new namespace`。
Compose 因此为 Worker 配置：

- `cap_add: SYS_ADMIN`
- `seccomp=unconfined`
- `apparmor=unconfined`
- `systempaths=unconfined`

Worker 主进程仍使用镜像内非 root 的 `appuser`。不要使用 `privileged: true`，
也不要通过清空 `WORKER_SANDBOX_COMMAND` 绕过任务隔离。部署前应运行真实代码任务，
确认 bubblewrap 能创建 namespace；容器健康检查本身不能证明任务沙箱可用。

### 生产不可信多租户任务：本 compose 画像不适用

`docker-compose.dev.yml` 与 `docker-compose.remote.yml` 为**开发/远程验收**画像，
存在多个不适合承接不可信多租户任务的边界：

- `--ro-bind / /` 把宿主容器整棵文件系统只读暴露给用户载荷。虽然
  `_credential_mask_dirs()` 用 tmpfs 掩掉了 `~/.ssh`、`~/.aws`、`/run/secrets`、
  `/var/run/secrets/kubernetes.io`、`/etc/kubernetes` 等已知路径，但任何未预见
  的 Secret 挂载点（例如自定义 CSI Driver 目录、Sidecar 写入的凭据文件）都不受
  掩蔽保护。
- `SYS_ADMIN` + `seccomp/apparmor/systempaths=unconfined` 是 bwrap 嵌套
  namespace 的硬需求，但在多租户生产语义下等价于允许任意 syscall 面。
- Rule 插件的 target_url 与自定义 headers/cookies 完全来自用户，如果 Worker
  与内部服务共享网络平面，任意规则任务都能对内部平面发起横向请求。

生产不可信多租户部署必须迁移到具备内核级隔离与网络策略的运行时：

- **运行时**：改用 gVisor (`runsc`) 或 Kata Containers 作为 Pod 运行时，剥离
  `SYS_ADMIN` 与所有 `unconfined` 开关；bwrap 层作为二级防御保留但不再是唯一
  边界。
- **网络策略**：Worker Namespace 拉起 default-deny NetworkPolicy，只放通对
  Master/Gateway 的必要端口，通过独立 egress-proxy 出网并按租户 ACL 收敛
  target_url 白名单。
- **Rule 网络**：`ANTCODE_RULE_ALLOW_NETWORK` 保持默认关闭；只在 egress-proxy
  就绪且规则任务经审核的租户上按需开启。
- **Node 分层**：不可信 Worker Pod 调度到独立 taint 的 Node Pool，与控制面
  和信任任务分离。

未落地这些前提前，**禁止把 dev/remote 画像直接投放到承接不可信任务的生产环境**。

## 生产部署合同 (compose.prod.yml)

round6 P0-03 后 K8s 决策废止，仓库以 `infra/docker/docker-compose.prod.yml`
作为**非 K8s 生产画像的最小起点**。它不是交钥匙方案；上线前必须完成
site-specific 落地：

1. `.env.production` 显式提供 `DATABASE_URL`/`REDIS_URL`/`POSTGRES_*`/
   `REDIS_PASSWORD`/`JWT_SECRET`/`ANTCODE_ENCRYPTION_KEY` 等生产密钥。
2. `ANTCODE_GATEWAY_TLS_DIR` 与 `ANTCODE_WORKER_TLS_DIR` 挂载真实 mTLS
   证书（server + ca + 每 Worker 独立 client 证书）；私钥不能进镜像。
3. `ANTCODE_REVERSE_PROXY_CONFIG` 指向 site 自建 nginx/traefik/caddy 配置，
   完成 HTTP→HTTPS 301、`connect-src` CSP、Gateway gRPC 转发。
4. `ANTCODE_TLS_CERTS_DIR` 挂载反向代理证书；cert-manager/acme.sh 自动续签。
5. `ANTCODE_IMAGE_REGISTRY` + `ANTCODE_IMAGE_TAG` 用 immutable digest 或语义
   化版本，禁止 `latest`；镜像必须经 Cosign 签名。
6. `backup` sidecar 骨架默认写 `/backup` 卷，site 通过 override 补 rclone /
   aws s3 上传 + 保留策略；`docker-compose.prod.backup.yml` 后续加入。
7. Prometheus scrape `/metrics` + alertmanager；Loki/ELK 收 json-file 日志。
8. Worker 承接不可信任务前必须落 gVisor/Kata + 独立宿主/网络域；此文件
   仍保留 `SYS_ADMIN` 是 bwrap 嵌套 namespace 硬需求，非独立宿主时
   仅适用于自有代码执行。

`compose.prod.yml` 相对 remote 画像的强制安全差异：

- `AUTH_COOKIE_SECURE=true`
- `REDIS_ACL_ENABLED=true`
- `ANTCODE_GATEWAY_ALLOW_INSECURE=false` + `WORKER_GATEWAY_TLS=true`
- `ANTCODE_RULE_ALLOW_NETWORK` 默认 `0`（Rule 断网），site 显式打开
- `ANTCODE_GATEWAY_LEGACY_SETTLE_UNTIL_TS=1`（legacy 通道关闭，
  round6 P1-GW-06）
- PG/Redis 不 `ports:` 对外，仅 `expose` 到 `antcode-internal` 网络
- Web API/Master `read_only: true` 根文件系统 + `/tmp` tmpfs

验证：`docker compose -f docker-compose.prod.yml config` 返回渲染 YAML
且不缺任何 `${?}` 必填变量。

## 远程验收传输模式

`docker-compose.remote.yml` 固定为 Direct Worker，避免同时注入 Gateway 和
Redis transport 配置。Gateway 验收必须叠加专用覆盖文件；覆盖文件会清空
`DATABASE_URL`、`REDIS_URL`、`WORKER_REDIS_URL` 和固定 `WORKER_ID`，再设置
Gateway endpoint。Gateway Worker 通过受认证、带 Lease fence 的 Gateway 通道
传输源码包和执行产物，不持有 PostgreSQL 或 Redis 根凭据。基础 Remote Worker
也不加载项目根 `.env`，防止 JWT、加密密钥等控制面凭据随模式覆盖一并泄露。
Gateway 首次注册必须先生成安装 Key，并通过当前 shell 的
`ANTCODE_WORKER_KEY` 临时注入。注册完成后 `unset ANTCODE_WORKER_KEY` 并重建
Worker，验证容器环境中不再存在明文 Key，且只依赖 `worker_data` 中持久化的
Gateway 注册凭据仍能启动。不要把一次性 Key 写入项目根 `.env`。

远程验收的 Master 单独设置 `ALLOW_PRIVATE_NODES=true`，仅用于读取 Compose
网络内的 E2E Git 服务。其他服务仍保持默认拒绝私网目标；正式生产配置不得
复制该测试开关。

```bash
# 以下命令均从项目根目录执行，项目根 .env 同时负责 Compose 插值和容器运行环境。
docker compose --env-file .env -p antcode-e2e \
  -f infra/docker/docker-compose.remote.yml build

# 全新数据库必须先显式初始化；Web API 启动本身不会自动建表。
docker compose --env-file .env -p antcode-e2e \
  -f infra/docker/docker-compose.remote.yml run --rm --no-deps \
  web-api python -m scripts.init_db

docker compose --env-file .env -p antcode-e2e \
  -f infra/docker/docker-compose.remote.yml up -d

docker compose --env-file .env -p antcode-e2e \
  -f infra/docker/docker-compose.remote.yml \
  -f infra/docker/docker-compose.remote.gateway.yml up -d --force-recreate worker
```

## 故障排查

### 容器启动失败

1. 执行 `docker compose -f docker-compose.dev.yml ps`
2. 查看异常服务日志
3. 检查 `.env` 是否缺少关键变量

### Worker 无法接单

1. 检查 Worker 与 Redis/Gateway 连通
2. 检查 Worker 传输模式配置
3. 检查 Web API 中 Worker 注册状态

### API 无法访问

1. 确认 `web-api` 健康检查通过
2. 检查端口是否被占用
3. 检查数据库与 Redis 依赖服务状态
