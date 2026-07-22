# AntCode K8s 部署骨架 (P0-02)

## 定位

`infra/k8s/` 提供 AntCode 的 **Kubernetes 生产部署骨架**,不是生产就绪的开箱即用画像。

- **骨架**:多副本 Deployment / StatefulSet + Service + Ingress + NetworkPolicy + PDB + Migration Job + 备份 CronJob,配 Kustomize base + production overlay。
- **未包含**:实际 Secret 值、镜像 immutable tag、存储类、集群特定的 Ingress annotation、TLS 证书链、备份对象存储凭据、监控/日志采集器接入、Argo/Helm 编排层。

如果你在找 `.env` 化的一体化画像,那是 `infra/docker/`(dev + remote 验收),**不适用于生产不可信多租户**。

## 快速校验

```bash
# 语法/schema 验证
kubectl kustomize infra/k8s/overlays/production/ \
  | kubectl apply --dry-run=client -f -
```

## 落地清单

### 1. Namespace + Secret

```bash
kubectl create namespace antcode

# 应用敏感值(从你的 secret manager 导入)
kubectl -n antcode create secret generic antcode-secrets \
  --from-literal=DATABASE_USER=antcode \
  --from-literal=DATABASE_PASSWORD='...' \
  --from-literal=REDIS_PASSWORD='...' \
  --from-literal=JWT_SECRET_KEY='...' \
  --from-literal=ENCRYPTION_KEY='...' \
  --from-literal=SESSION_SECRET='...' \
  --from-literal=WORKER_INSTALL_KEY='...'
```

### 2. Gateway / Worker mTLS

Gateway 生产必须走 mTLS:

```bash
kubectl -n antcode create secret tls gateway-tls \
  --cert=server.crt --key=server.key

# Worker 客户端证书 + CA
kubectl -n antcode create secret generic worker-tls \
  --from-file=ca.crt \
  --from-file=client.crt \
  --from-file=client.key
```

证书生成参见 `docs/mtls-deployment.md`。

### 3. 镜像 immutable tag

`overlays/production/kustomization.yaml` 的 `images:` 段固定为 semver:

```yaml
images:
  - name: antcode/gateway
    newTag: 1.2.3
    digest: sha256:abc...
```

生产禁止使用 `:latest`。

### 4. RuntimeClass (P0-03 关键)

**不可信任务的沙箱边界不能只靠 bwrap + SYS_ADMIN。** 生产必须叠加 gVisor 或 Kata Containers:

```yaml
# overlays/production/patches/worker-runtime-class.yaml (已包含)
spec:
  template:
    spec:
      runtimeClassName: runsc
      nodeSelector:
        antcode.io/worker-pool: untrusted
```

前置条件:
- 集群安装了 `gvisor-node-installer` 或 Kata Containers operator
- 注册了 `RuntimeClass runsc` (或 `kata-containers`)
- 有 taint 隔离的独立 Node Pool 承载不可信 Worker Pod

### 5. StorageClass

`base/postgres-statefulset.yaml` 与 `base/redis-statefulset.yaml` 的 PVC 未指定 `storageClassName`,依赖集群默认。overlay 应显式指定(如 `gp3-ssd`、`premium-rwo`):

```yaml
# overlays/production/patches/pg-storage.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: data-postgres-0
spec:
  storageClassName: gp3-ssd
```

### 6. Ingress host + cert-manager

`base/ingress.yaml` 的 host 是占位符 `antcode.example.com`,overlay 覆盖为真实域名并接入 cert-manager:

```yaml
metadata:
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
```

Gateway 生产建议直接用 `Service type=LoadBalancer` + 云商 TLS,而非常规 HTTP Ingress。

### 7. 备份上传

`base/backup-cronjob.yaml` 的 command 只创建本地 dump,**没有上传**。生产应扩展 command 或改用 Velero / CloudNativePG / 托管数据库自动备份。

### 8. NetworkPolicy 前提

`base/networkpolicy.yaml` 需要:
- CNI 支持 NetworkPolicy(Calico / Cilium / Antrea,非默认 kubenet)
- Ingress controller Namespace 有 label `kubernetes.io/metadata.name: ingress-nginx`

如果集群 CNI 不支持,`networkpolicy.yaml` 会被静默忽略,失去"默认拒绝"保护。

### 9. 监控 / 日志采集

未提供 ServiceMonitor / Fluentd sidecar 等,应按你使用的采集栈(Prometheus Operator / Datadog / OpenTelemetry Collector)在 overlay 追加。

## 剩余风险

| 项 | 说明 |
|---|---|
| **secrets 没走 SealedSecret / SOPS** | 骨架用明文 `kubectl create secret`;生产应改用 GitOps 友好的加密 secret 方案(Sealed Secrets / SOPS / external-secrets) |
| **Redis 单副本** | 生产建议 Redis Cluster / Sentinel 或托管方案;Direct 模式还需 ACL slot 迁移能力(见 `docs/redis-cluster.md`) |
| **PG 单副本** | 生产建议托管 PG (RDS / Cloud SQL) 或 CloudNativePG operator |
| **Worker `SYS_ADMIN`** | bwrap 嵌套 namespace 硬需求;仅在 gVisor / Kata 上叠加 SYS_ADMIN 才安全,否则等同 root |
| **备份 CronJob 未上传** | 见落地清单第 7 项 |
| **无 HPA / VPA** | 未包含自动扩缩;overlay 应按流量画像追加 HPA |

## 与 compose 的关系

| 场景 | 用什么 |
|---|---|
| 本地开发 | `docker compose -f infra/docker/docker-compose.dev.yml up` |
| 远程验收 / E2E | `docker compose -f infra/docker/docker-compose.remote.yml up` |
| 生产 | `infra/k8s/overlays/<site>/` (本骨架) |

三者互不共享配置,**不要期望 `.env` 能跨切**。

## 未闭环项(下一轮)

- Helm chart / Argo CD Application 定义(当前只有 Kustomize)
- Prometheus ServiceMonitor + Grafana Dashboard as-code
- Chaos test 场景(kill Master leader、断 Redis、断 Gateway 各一次)
