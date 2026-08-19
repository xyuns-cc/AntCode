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
| `REDIS_NAMESPACE` | Redis key namespace；部署后保持稳定，生产 Compose 必填 |
| `WORKER_TRANSPORT_MODE` / `WORKER_NAME` | Worker 基础配置 |
| `ANTCODE_TRUSTED_PROXIES` | 仅开发/自定义 Compose 使用的受信直接上游；默认留空 |

## 反向代理客户端 IP

Web API 默认不信任 `X-Forwarded-For` 和 `X-Real-IP`，而是使用 socket 对端
地址。自定义 Nginx、Ingress 或负载均衡器接入时，必须把**与 Web API 直接建立
连接的实际代理**精确 IP 配置到 `ANTCODE_TRUSTED_PROXIES`。

生产 Compose 的 Web API 直接 socket 上游不是公网 `reverse-proxy`，而是同时加入
`antcode-edge` 和 `antcode-control` 的 frontend。生产文件因此忽略人工填写的
`ANTCODE_TRUSTED_PROXIES`，只信任 `ANTCODE_FRONTEND_CONTROL_IP` 的精确地址。
公网 reverse-proxy 先丢弃客户端提交的转发链并写入真实来源，frontend 只信任
`ANTCODE_REVERSE_PROXY_EDGE_IP`，再把验证后的单一来源传给 Web API。两个地址都由
专用 bridge 的静态 IPAM 约束，不能改成整个 bridge CIDR。

不要填写 `0.0.0.0/0`、`::/0`、客户端网段或无法确认归属的共享网段。宽泛信任
会允许外部请求伪造来源 IP，破坏登录限流、审计记录和 Worker 安装来源绑定。
无法固定代理地址时应先固定部署网络或 Ingress 出口，再配置精确地址；不要为了让
转发头生效而放宽信任范围。

## Worker 沙箱要求

Worker 使用 bubblewrap 的非特权 user namespace 隔离任务。所有 Compose 画像均
`cap_drop: ALL`、`no-new-privileges:true`、根文件系统只读、以非 root 用户运行，
且**不授予 `SYS_ADMIN`**。

宿主机必须允许非特权 user namespace（`kernel.unprivileged_userns_clone=1`、
`user.max_user_namespaces` 足够大；Ubuntu 24.04+ 还需
`kernel.apparmor_restrict_unprivileged_userns=0`）。

但仅有宿主策略**不够**——在 Docker 下还必须放宽三项容器级限制，缺任何一项
每个任务都会以 `bwrap: No permissions to create new namespace` 或
`Failed to make / slave` 失败（已在真实宿主逐项验证）：

| 项 | 原因 | 本仓做法 |
| --- | --- | --- |
| seccomp | 内置 profile 在无 `CAP_SYS_ADMIN` 时禁止 `clone`/`unshare` 带 `CLONE_NEWUSER` | `seccomp/worker-userns.json`：内置 profile **仅**追加 namespace 相关调用，**不是** `unconfined` |
| AppArmor | `docker-default` 直接 `deny mount` | `apparmor=unconfined` |
| system paths | Docker 对 `/proc` 的屏蔽挂载使 userns 内挂 procfs 失败 | `systempaths=unconfined` |

这三项只解除"容器阻止进程给自己建更严格的沙箱"的限制，不授予任何宿主权限：
进程仍然 `cap_drop: ALL`、非 root、只读根，且随后会被 bwrap 关进更小的
namespace。**不要**改回 `SYS_ADMIN` 或 `seccomp=unconfined`——那是完全不同的
风险等级。契约测试 `tests/unit/core/test_worker_sandbox_security_contract.py`
会锁住这组配置，防止后续"加固"再次静默关掉整个执行面。

健康检查只证明 Worker 控制循环和 transport ready。每个目标宿主仍必须执行真实
Python、Rule、Playwright 任务，确认 namespace、只读根、网络隔离、资源限制和取消
语义都实际生效。

### 生产不可信多租户任务：本 compose 画像不适用

`docker-compose.dev.yml` 只是开发/功能验收画像，Direct Worker 会与 Redis 共网，
不能承接不可信生产任务。非 K8s 生产路径把 Worker 部署到
独立宿主机或虚拟机，只加载 `docker-compose.prod.worker.yml`；该宿主不挂载数据库、
Redis、控制面数据卷或服务端密钥，仅通过 mTLS Gateway 和公网 HTTPS API 通信。
默认关闭 Rule 网络。高风险租户还必须使用独立 Worker 宿主，不能只依赖同一内核内
的 bubblewrap 作为租户边界。

## 生产部署合同 (docker-compose.prod.yml)

项目不使用 K8s。生产控制面使用
`infra/docker/docker-compose.prod.yml`，该文件是完整启动依赖图，不再是省略
frontend、数据库初始化或 mTLS bootstrap 参数的骨架。

**本仓库不再包含任何自动化发布流水线，也不再产出 registry 镜像。** 五个应用镜像
（web-api / master / gateway / worker / frontend）由生产 Compose 自己的 `build:` 段在
部署机上就地构建：`deploy-production.sh` 先 `docker compose build`，再
`docker compose pull --ignore-buildable` 拉取仍按 digest pin 的第三方运行时镜像
（postgres / redis / 反向代理，权威取值见 `release-runtime-images.json`）。

**代价必须认清**：本地构建没有不可变产物，也没有签名与来源证明——cosign 验签链路
（`verify-production-images.sh` 与 release collection 元数据镜像）已随发布链路一并删除，
信任边界因此变成**部署机本身与它的源码树**。回滚不能再切回一个旧 digest，只能 revert
源码后重新构建，回滚窗口要把构建时长算进去。

因此 `ANTCODE_IMAGE_TAG` 必须每次发布唯一，**推荐直接用被部署的 40 位 Git commit**：
它是把运行中的容器对回一次确定源码构建的唯一线索。复用同一个 tag 会让 `up -d` 认为
镜像没变而不重建容器，等于发布静默失效。

`make docker-buildx` 仅在 `build/docker`（或 `BUILDX_OUTPUT_DIR`）生成本地多架构 OCI
归档，不登录 registry、不推送镜像，也不是生产部署入口（生产走 Compose 的 `build:` 段）；
传入旧的 `BUILDX_REGISTRY`/`BUILDX_TAG` 参数会显式失败，避免旧脚本误报发布成功。

生产 `.env.production` 只保存镜像 tag / digest、端口、资源值和 secret **文件路径**，
不保存 secret 内容。数据库 URL、Redis URL、PostgreSQL 密码、Redis health 密码、
JWT 和加密密钥全部通过 Docker secrets 只读挂载。应用镜像的固定入口脚本严格读取
`*_FILE` 后再启动应用；缺文件、不可读或同时配置 inline 值都会直接失败。

1. 把部署机源码树切到要发布的 revision（`git status --porcelain` 必须为空——工作区脏
   等于发布了一份无法复现的镜像），并把 `ANTCODE_IMAGE_TAG` 设为该 revision 的 40 位
   commit。第三方运行时镜像另按 `release-runtime-images.json` 设置
   `ANTCODE_{POSTGRES,REDIS,REVERSE_PROXY}_IMAGE_REPOSITORY` 与对应 `_IMAGE_DIGEST`
   （digest 只填 64 位十六进制正文，不带 `sha256:` 前缀）。五个应用镜像没有 digest 变量，
   它们由 Compose 就地构建。
2. 在 `.env.production` 显式设置稳定的 `REDIS_NAMESPACE`；它即使在工具 profile
   未启用时也是 Compose 必填插值，必须与运行中服务使用的 namespace 完全一致。
   在仓库外创建 `ANTCODE_DATABASE_URL_FILE`、`ANTCODE_REDIS_URL_FILE`、
   `ANTCODE_POSTGRES_PASSWORD_FILE`、`ANTCODE_REDIS_HEALTHCHECK_PASSWORD_FILE`、
   `ANTCODE_ENCRYPTION_KEY_FILE`、`ANTCODE_ENCRYPTION_KEY_SALT_FILE`、
   `ANTCODE_ENCRYPTION_KEYS_LEGACY_FILE` 和 `ANTCODE_JWT_SECRET_FILE`。无旧密钥时
   legacy 文件保持空文件；所有文件限制为部署账号和 Docker daemon 可读。
3. 从 `redis/users.acl.example` 在仓库外创建真实 ACL 文件，分别替换管理账号
   和健康检查账号密码。`REDIS_URL` 必须使用管理账号，
   `REDIS_HEALTHCHECK_*` 使用 health 账号。`ANTCODE_REDIS_ACL_DIR` 指向一个
   仓库外、Redis 容器用户可写且包含 `users.acl` 的目录；应用执行
   `ACL SAVE` 时会在该目录创建临时文件并原子替换 ACL 文件。
4. 显式设置公网 HTTPS `ANTCODE_PUBLIC_API_BASE_URL`、公网 mTLS
   `ANTCODE_GATEWAY_HOST` / `ANTCODE_GATEWAY_PUBLIC_PORT`，以及 HTTPS
   `ANTCODE_WORKER_INSTALL_SOURCE_URL`、完整 40 位 commit
   `ANTCODE_WORKER_INSTALL_SOURCE_REF` 和三段固定版本
   `ANTCODE_WORKER_INSTALL_UV_VERSION`。生产强制生成的 Worker 配置启用 Gateway
   TLS；任一值缺失时 Compose 拒绝解析，URL、commit、版本、主机或端口无效时 Web
   API 在 readiness 前启动失败，不会到生成安装 Key 时才返回 503。
5. `ANTCODE_GATEWAY_TLS_DIR` 挂载 `server.crt/server.key/ca.crt`；
   `ANTCODE_WORKER_TLS_DIR` 挂载该 Worker 独立的
   `client.crt/client.key/ca.crt`。私钥不得进入镜像或仓库。
6. 仓库内受审查的 `nginx.prod.conf` 固定启用 TLS 1.2/1.3、HTTP/2、安全头、API
   限流和 SSE 禁缓冲。反代镜像必须是固定 digest 的 unprivileged Nginx，Compose
   以 UID/GID 101 运行并把容器 8080/8443 映射到宿主 80/443，不需要低端口
   capability。`ANTCODE_TLS_CERTS_DIR` 必须提供 UID 101 可读的 `tls.crt`/`tls.key`，
   `ANTCODE_CONTROL_SUBNET` 与 `ANTCODE_EDGE_SUBNET` 必须是互不重叠的部署专用网段；
   `ANTCODE_FRONTEND_CONTROL_IP`、`ANTCODE_REVERSE_PROXY_EDGE_IP` 必须分别位于对应
   网段内、且**落在该网段的 `ip_range` 动态池之外**——Docker 的动态 IPAM 从网段低位
   起分配且不知道同项目里谁 pin 了地址，pin 在动态池内会被先创建的容器（中间件先于
   frontend、frontend 先于 reverse-proxy）抢走，首次部署直接
   `failed to set up container networking: Address already in use`。动态池由
   `ANTCODE_CONTROL_DYNAMIC_RANGE` / `ANTCODE_EDGE_DYNAMIC_RANGE` 声明，二者都必须是
   对应网段的真子网，并为控制面容器留足地址。Web API 只信任 frontend 的精确 control IP。
7. Gateway 仅发布 mTLS 端口。`ANTCODE_GATEWAY_BIND_ADDRESS` 必须绑定专用地址，
   宿主防火墙只允许 Worker 主机；每个 Worker 使用独立客户端证书目录。
8. 控制面、执行面、边缘面和服务出站使用不同 bridge。PostgreSQL/Redis 只在
   `internal:true` 控制网络；默认控制面部署不启动共置 Worker。每个 Worker 用
   独立宿主/虚拟机、Compose project、名称、容器名、数据卷和 mTLS 证书部署，
   禁止 `docker compose --scale worker=N`。
9. 生产必须另行接入受监控的异机/对象存储备份和恢复演练。本地备份 override
   默认禁用，不能当成灾备。

部署机必须能完整构建本仓镜像（Docker + 到基础镜像 registry 的网络）。脚本冻结环境文件，
先 `compose build` 构建五个应用镜像、再 `compose pull --ignore-buildable` 拉取按 digest pin
的第三方镜像；两步都排在 `stop` 之前，任一步失败时正在跑的控制面一个容器都还没被动过。
随后停止 writer、执行 Redis 门禁和数据库 migration；任一步失败都会中止，全部成功后才启动
长期服务：

数据库 migration 固定先执行标准 `scripts.init_db`，再在 writer 仍停止时执行
`scripts.migrate_worker_install_keys`；两步都使用本轮构建出的同一个 Web API 镜像 tag。

全域主加密密钥轮换使用独立的显式模式，不会在普通部署中自动执行。该模式会构建镜像、停止所有 writer，
依次执行离线 dry-run、apply 和 primary-only，并在成功后继续保持 writer 停止，供运维删除 legacy keyring：

```bash
infra/docker/deploy-production.sh .env.production rotate-encryption-key --confirm-writers-stopped
```

命令成功不自动删除 secret file，也不自动重启长期服务；必须先确认新密钥已生效，再撤销并
删除 legacy secret file，复验后使用正常部署入口启动。

```bash
infra/docker/deploy-production.sh .env.production fresh-deploy
```

`fresh-deploy` 只适用于全新 Redis；检测到旧 key、当前 Crawl 数据、未排空执行队列
或不受支持 envelope 时会拒绝启动控制面。既有环境必须先停止所有独立 Worker 和
其他仓库外 Redis writer、完成 Redis 备份，再执行 dry-run。脚本也会停止本
Compose 的 writer；dry-run 成功后仍保持它们停止，不会自动恢复服务：

```bash
infra/docker/deploy-production.sh .env.production existing-upgrade \
  --confirm-writers-stopped \
  --paused-project project-1 \
  --paused-batch project-1:batch-1
```

人工核对 JSON 报告的 `safe`、key、类型、数量、PEL、lag 和目标 key 后，在没有恢复
任何 writer、没有改变暂停声明的前提下，使用完全相同参数增加两个显式确认。工具
会重新执行完整预检，只有仍安全时才迁移，随后执行数据库 migration 并恢复整栈：

```bash
infra/docker/deploy-production.sh .env.production existing-upgrade \
  --confirm-writers-stopped \
  --paused-project project-1 \
  --paused-batch project-1:batch-1 \
  --apply \
  --preflight-reviewed
```

`--apply` 缺少 `--preflight-reviewed`、既有升级缺少停写确认、报告有 blocker 或 Redis
写入/校验失败、schema 或安装 Key 迁移失败都会返回非零，writer 保持停止。安装 Key
迁移若找不到 pending Key 的 Redis 来源元数据也会明确失败，不会把来源限制降级为空。
独立 Worker 不属于控制面 Compose，
脚本无法替运维方停止或证明其状态；`--confirm-writers-stopped` 是对所有外部 writer
也已停机的明确确认。

所有长期服务都设置 CPU、内存、PID、只读根和日志轮转边界；Redis 使用
`maxmemory` + `noeviction`。readiness 失败会向 PID 1 发 TERM，让
`restart: unless-stopped` 在运行期实际自愈，而不是永久停留在 unhealthy。

### 一次性管理员与 Worker bootstrap

常驻 Compose 不含默认管理员密码或 Worker 安装 Key。新库使用已验证的原子发布集合
启动 PostgreSQL/Redis 并执行一次性管理员 migration，成功后立即删除宿主密码文件：

```bash
infra/docker/bootstrap-admin.sh .env.production
```

管理员 bootstrap 必须在 E2E 前独立成功。E2E 进程只接收显式公网 HTTPS 地址、
管理员用户名/口令和 Worker ID，通过登录 API 验证该前置结果；它不接收数据库连接
串，也不会直接创建管理员、重置口令、提权或启用账号。登录失败即说明 bootstrap 或
部署合同失败，测试不会修改数据库把环境“修好”。仅本机回环 CI 允许显式 HTTP URL。

控制面部署成功后，在每台独立 Worker 主机生成该 Worker 专属安装 Key 文件并运行：

```bash
infra/docker/bootstrap-worker.sh .env.production
```

脚本只在 bootstrap 容器达到 healthy 后才删除该容器，并用同一独立数据卷重建不
挂载安装 Key 的正式 Worker；失败会返回非零。成功后立即删除宿主安装 Key 文件。
后续升级使用同一环境快照完成 collection 校验、pull 和启动，不能把多个实例 scale
到同一 service：

```bash
infra/docker/deploy-worker.sh .env.production
```

只用于本机短期留存的备份 profile：

```bash
docker compose --env-file .env.production \
  -f infra/docker/docker-compose.prod.yml \
  -f infra/docker/docker-compose.prod.local-backup.yml \
  --profile local-backup up -d backup-local
```

该任务从 Docker secret 读取数据库 URL。启动时删除残留 `.partial`；每次 dump 和
结构校验都有硬超时，失败 trap 清理临时文件；成功后原子发布 dump、SHA-256 和
`.last-success`。healthcheck 按 `BACKUP_MAX_AGE_SECONDS` 检查最近成功时间，过期会
退出并重启。CPU、内存、PID 和只读根边界与主栈一致。

本地备份不是异机灾备。至少每次发布和每个备份策略周期都要新建一个名称严格匹配
`antcode_restore_test_*` 或 `antcode_restore_ci_*` 的隔离数据库，再运行恢复演练。脚本
会向 PostgreSQL 查询 `current_database()` 并与显式名称逐字比较，任一名称门禁失败都
发生在 `pg_restore --clean` 之前。`--` 后必须传入本次候选版本的真实 migration
命令，不能用空命令或模拟命令代替：

```bash
export RESTORE_READINESS_URL='http://127.0.0.1:18080/api/v1/health/ready'
infra/docker/verify-backup-restore.sh \
  /backup/antcode-YYYYmmddTHHMMSSZ.dump \
  "$RESTORE_DATABASE_URL" \
  antcode_restore_test_release_20260730 \
  -- uv run python -m scripts.init_db
```

脚本先验证 checksum，再以 `--clean --single-transaction --exit-on-error` 原子恢复，
把恢复库 URL 只注入 migration 子进程，随后再次核对实际库名。后验会检查当前核心
表、索引 valid/ready、约束 validated、明文 Worker 凭据列已删除、敏感数据迁移
ledger、基础系统配置和用户角色一致性，并输出 users/workers/projects/tasks/runs/
configs/audit 的精确行数作为演练证据。配置 `RESTORE_READINESS_URL` 时，该 URL 必须
属于只连接本次恢复库和测试 Redis 的候选 Web API，脚本还会要求其真实 readiness
通过；不能指向现网 Web API。未配置时脚本只明确报告“数据库恢复演练通过、应用
readiness 未验证”，不得记录为全栈恢复成功。

该脚本只允许销毁专用 test/ci 演练库，不是生产数据库原地恢复工具，也不能自动证明
RPO、异机副本、对象存储保留、DNS/证书切换或生产流量恢复。生产事故恢复必须执行经
审批的独立 runbook，并分别记录备份生成时间、源端关键表计数、恢复后的脚本输出、
RTO、应用 readiness/业务 smoke test 和异机副本证据；不得把本脚本退出码单独称为
生产恢复验收通过。

## 测试机验收

仓库不再维护单独的远程验收 Compose override。测试机上的开发功能验收使用
`docker-compose.dev.yml`；它固定把 HTTP、PostgreSQL、Redis 和明文 Gateway 端口
绑定到回环地址，只能通过 SSH 隧道或测试机本机访问，不得直接暴露到测试网或公网。

以下命令均从项目根目录执行，并使用独立的 Compose project。`down -v` 会删除该
测试 project 的数据库、Redis 和应用卷，只能用于已确认无需保留数据的测试环境：

```bash
docker compose --env-file .env -p antcode-e2e \
  -f infra/docker/docker-compose.dev.yml down -v --remove-orphans
docker compose --env-file .env -p antcode-e2e \
  -f infra/docker/docker-compose.dev.yml build
docker compose --env-file .env -p antcode-e2e \
  -f infra/docker/docker-compose.dev.yml up -d --wait \
  postgres redis web-api master gateway frontend

# 在 Web 界面生成一次性安装 Key，仅注入当前 shell 后启动 Direct Worker。
export ANTCODE_WORKER_KEY='<one-time-install-key>'
docker compose --env-file .env -p antcode-e2e \
  -f infra/docker/docker-compose.dev.yml up -d --wait worker
unset ANTCODE_WORKER_KEY
```

需要验证生产传输、安全边界和升级编排时，必须使用本页“生产部署合同”中的镜像 tag /
第三方 digest、Docker secrets 与 TLS/mTLS 配置，并执行真实部署入口：

```bash
infra/docker/deploy-production.sh .env.production fresh-deploy
```

该路径使用 `docker-compose.prod.yml`，生产 Worker 继续按前述
`docker-compose.prod.worker.yml` 的独立宿主合同部署。不能用开发 Compose 的成功
替代生产等价验收，也不能绕过部署脚本手工启动生产服务。

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
