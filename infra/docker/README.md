# AntCode Docker 开发部署

## 目标

使用 `docker-compose.dev.yml` 拉起本地联调环境，覆盖：

- `web_api`（Control Plane）
- `master`（Schedule Plane）
- `gateway`（Data Plane）
- `worker`（Execution Plane）
- `postgres` / `redis` / `frontend`

## 快速启动

```bash
cd infra/docker
cp .env.example .env
docker compose -f docker-compose.dev.yml up -d postgres redis web-api master gateway frontend
```

登录 Web 界面生成一次性 Worker 安装 Key，写入 `.env` 的
`ANTCODE_WORKER_KEY` 后再启动执行面：

```bash
docker compose -f docker-compose.dev.yml up -d worker
```

Worker 注册完成且凭据已写入 `worker_data` 后，删除 `.env` 中的一次性 Key。
后续重建会复用持久凭据；若首次注册尚未完成，Compose 会显式拒绝空 Key，
不会以反复重启伪装为可用环境。

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

## 生产部署合同 (docker-compose.prod.yml)

K8s 路径已废止。非 K8s 生产部署使用
`infra/docker/docker-compose.prod.yml`，该文件是完整启动依赖图，不再是省略
frontend、数据库初始化或 mTLS bootstrap 参数的骨架。

生产变量通过 Compose 的 `--env-file` 只用于插值。Gateway 和 Worker 不读取
整份环境文件；尤其 Gateway Worker 不会收到 `DATABASE_URL`、`REDIS_URL`、
`JWT_SECRET` 或应用加密密钥。上线前必须完成以下配置：

1. 为每个应用和中间件分别提供 `ANTCODE_*_IMAGE_REPOSITORY` 与
   `ANTCODE_*_IMAGE_DIGEST`。digest 只填写 64 位十六进制正文；生产 Compose
   强制拼为 `registry/repository@sha256:<digest>`，不接受 tag，也不在服务器
   上构建镜像。
2. 配置 `DATABASE_URL`、`POSTGRES_*`、`REDIS_URL`、`ANTCODE_JWT_SECRET_FILE`、
   `ENCRYPTION_KEY`、`ENCRYPTION_KEY_SALT` 和首次管理员密码。
   `ANTCODE_JWT_SECRET_FILE` 是宿主机上不进入仓库的密钥文件；Compose 将其
   作为只读 Docker secret 挂载到 migration / web-api 的
   `/run/secrets/jwt_secret`，不会向容器注入 inline `JWT_SECRET`。
3. 从 `redis/users.acl.example` 在仓库外创建真实 ACL 文件，分别替换管理账号
   和健康检查账号密码。`REDIS_URL` 必须使用管理账号，
   `REDIS_HEALTHCHECK_*` 使用 health 账号。`ANTCODE_REDIS_ACL_DIR` 指向一个
   仓库外、Redis 容器用户可写且包含 `users.acl` 的目录；应用执行
   `ACL SAVE` 时会在该目录创建临时文件并原子替换 ACL 文件。
4. `ANTCODE_GATEWAY_TLS_DIR` 挂载 `server.crt/server.key/ca.crt`；
   `ANTCODE_WORKER_TLS_DIR` 挂载该 Worker 独立的
   `client.crt/client.key/ca.crt`。私钥不得进入镜像或仓库。
5. `ANTCODE_REVERSE_PROXY_CONFIG` 和 `ANTCODE_TLS_CERTS_DIR` 提供生产反向
   代理配置及证书，并把代理容器实际 CIDR 写入 `ANTCODE_TRUSTED_PROXIES`。
6. 把站点对外 HTTPS API 根地址写入 `ANTCODE_PUBLIC_API_BASE_URL`。首次启动前
   生成一次性 `ANTCODE_WORKER_KEY`；Worker 通过该 HTTPS 地址注册和 ACK，凭据
   写入 `worker_data` 后从环境文件删除安装 Key并强制重建 Worker。生产路径不
   允许用 Compose 内部明文 HTTP 传输安装 Key或永久凭据。
7. Worker 容器的 `SYS_ADMIN` 仅用于 bubblewrap。执行不可信租户代码时必须
   使用独立宿主和网络域；默认 `ANTCODE_RULE_ALLOW_NETWORK=0`。
8. 生产必须另行接入受监控的异机/对象存储备份和恢复演练。本地备份 override
   默认禁用，不能当成灾备。

启动前先渲染配置；缺少任何必填变量时命令必须失败：

```bash
docker compose --env-file .env.production \
  -f infra/docker/docker-compose.prod.yml config --quiet

docker compose --env-file .env.production \
  -f infra/docker/docker-compose.prod.yml up -d
```

`migration` 必须成功退出后 Web API、Master 和 Gateway 才会启动；frontend
等待 Web API readiness，Worker 等待 Web API 与 Gateway readiness。Gateway
固定使用 mTLS，Worker 固定为 backendless Gateway transport。

只用于本机短期留存的备份 profile：

```bash
docker compose --env-file .env.production \
  -f infra/docker/docker-compose.prod.yml \
  -f infra/docker/docker-compose.prod.local-backup.yml \
  --profile local-backup up -d backup-local
```

该任务直接使用应用的同一个 `DATABASE_URL`。它先写 `.partial`，用
`pg_restore --list` 验证 custom dump，计算 SHA-256 后再原子改名，并按
`BACKUP_RETENTION_DAYS`（默认 14 天）清理 dump 和校验和；任一步失败都会使
容器退出，不会打印虚假成功。它仍没有远端上传和独立恢复监控，因此不满足
生产灾备要求。

## 远程验收传输模式

`docker-compose.remote.yml` 与生产数据面一致，固定使用 Gateway backendless
Worker。Worker 通过受认证、带 Lease fence 的 Gateway 通道传输源码包和执行
产物，不持有 PostgreSQL 或 Redis 根凭据，也不加载项目根 `.env`，防止 JWT、
加密密钥等控制面凭据泄漏到执行面。保留的
`docker-compose.remote.gateway.yml` 只用于兼容旧验收命令，不再承担安全覆盖。
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

# 已有持久凭据的 Worker 重建时，不再需要一次性安装 Key。
unset ANTCODE_WORKER_KEY
docker compose --env-file .env -p antcode-e2e \
  -f infra/docker/docker-compose.remote.yml up -d --force-recreate worker
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
