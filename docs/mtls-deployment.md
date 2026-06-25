# mTLS 部署指南

> Gateway 模式下,Worker 通过双向 TLS 接入。本文档说明 CA / Server / Worker
> 证书的签发流程、Gateway / Worker 端配置项,以及代码层 CN/SAN ↔ worker_id
> 绑定校验的行为。

---

## 何时启用

- **Direct 模式** (Worker 直连 Redis,内网部署):**不**需要 mTLS,内网隔离即可
- **Gateway 模式** (Worker 跨公网接入):**强烈建议**启用 mTLS,与 API Key /
  JWT 形成深度防御

API Key + JWT 单独使用足够防"未授权访问",但 mTLS 额外提供:
- **传输层加密**(无窃听 / 中间人)
- **客户端身份强绑定**(证书 CN/SAN 必须匹配 metadata 的 `x-worker-id`)
- **CA 撤销机制**(被泄露的 Worker 证书可吊销)

---

## 证书签发

### 1) 自建 CA(一次性)

```bash
# 生成 CA 私钥 + 自签证书 (10 年)
openssl req -x509 -newkey rsa:4096 -nodes \
  -keyout ca.key -out ca.crt \
  -days 3650 \
  -subj "/CN=AntCode-Internal-CA"
```

CA 私钥 (`ca.key`) **必须离线保存**,只在签发新证书时使用。

### 2) Gateway 服务器证书

```bash
# Gateway 私钥
openssl genrsa -out gateway.key 4096

# CSR (SAN 必须包含所有 Gateway 监听域名 / IP)
openssl req -new -key gateway.key -out gateway.csr \
  -subj "/CN=gateway.antcode.internal" \
  -addext "subjectAltName=DNS:gateway.antcode.internal,DNS:gateway,IP:10.0.0.10"

# CA 签发 (有效期 1 年)
openssl x509 -req -in gateway.csr \
  -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out gateway.crt -days 365 \
  -extfile <(printf "subjectAltName=DNS:gateway.antcode.internal,DNS:gateway,IP:10.0.0.10")
```

### 3) Worker 客户端证书(**每个 Worker 一份**)

```bash
# Worker 私钥
openssl genrsa -out worker-<worker-id>.key 4096

# CSR: CN 必须等于 worker_id,SAN 也带上
WORKER_ID=worker-prod-001
openssl req -new -key worker-${WORKER_ID}.key -out worker-${WORKER_ID}.csr \
  -subj "/CN=${WORKER_ID}" \
  -addext "subjectAltName=DNS:${WORKER_ID}"

# CA 签发
openssl x509 -req -in worker-${WORKER_ID}.csr \
  -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out worker-${WORKER_ID}.crt -days 365 \
  -extfile <(printf "subjectAltName=DNS:${WORKER_ID}")
```

⚠️ **关键约束**: 证书的 `CN` **或** `subjectAltName` (DNS 条目) **必须等于
Worker 启动时传入的 `worker_id`**。Gateway 的 `AuthInterceptor` 会从
`context.auth_context()` 读取证书 CN/SAN,与 metadata 中的 `x-worker-id`
做精确字符串比对,**不匹配则返回 PERMISSION_DENIED**(详见
[`services/gateway/src/antcode_gateway/auth.py`](../services/gateway/src/antcode_gateway/auth.py)
的 `_check_mtls_binding`)。

---

## 配置项

### Gateway 端(环境变量)

| 变量 | 说明 | 必填 |
|---|---|---|
| `GRPC_TLS_CERT_PATH` | Gateway 服务器证书 `gateway.crt` 路径 | TLS 必填 |
| `GRPC_TLS_KEY_PATH` | Gateway 服务器私钥 `gateway.key` 路径 | TLS 必填 |
| `GRPC_TLS_CA_PATH` | CA 证书 `ca.crt` 路径(用于校验 Worker 客户端证书) | **mTLS 必填** |
| `AUTH_ENABLED` | 是否启用 AuthInterceptor (API Key / JWT / mTLS) | 默认 `true` |

`tls_cert_path + tls_key_path` 都设 → TLS;再加 `tls_ca_path` → mTLS
(代码层会自动 `require_client_auth=True`,见 [`server.py:163`](../services/gateway/src/antcode_gateway/server.py))。

### Worker 端(`worker_config.yaml` 或 env)

```yaml
transport:
  mode: gateway
  gateway:
    endpoint: "gateway.antcode.internal:50051"
    tls:
      enabled: true
      ca_path: /etc/antcode/ca.crt              # 验证 Gateway 服务器证书
      client_cert_path: /etc/antcode/worker.crt # mTLS 客户端证书
      client_key_path: /etc/antcode/worker.key  # mTLS 客户端私钥
  worker_id: worker-prod-001                    # 必须等于客户端证书 CN/SAN
```

证书文件权限建议 `chmod 0600 worker.key`,owner 是 Worker 进程用户。

---

## 部署 checklist

- [ ] CA 私钥离线保存,签发流程有审计
- [ ] Gateway 证书 SAN 包含所有访问入口(域名 + IP)
- [ ] 每个 Worker 有独立证书,CN/SAN = `worker_id`
- [ ] 证书 `private key` 文件 `chmod 0600`,owner 限定
- [ ] CA 证书分发给所有 Worker(用于校验 Gateway 服务器证书)
- [ ] Gateway 容器 mount 三份证书文件到 `/etc/antcode/`
- [ ] 设置三个环境变量 `GRPC_TLS_CERT_PATH` / `GRPC_TLS_KEY_PATH` / `GRPC_TLS_CA_PATH`
- [ ] 启动 Gateway,日志看到 `mTLS 已启用` / `require_client_auth=True`
- [ ] 启动 Worker,日志看到 `gRPC channel 已建立 (mTLS)`
- [ ] Worker 完成 `ControlService.Register` (Gateway 日志看到 Register 通过)

---

## 验证

### 服务器侧

```bash
# Gateway 启动后,确认监听了 TLS 端口且要求客户端证书
openssl s_client -connect gateway.antcode.internal:50051 \
  -showcerts -CAfile ca.crt
# 预期: 服务器返回握手成功,要求客户端提供证书
```

### Worker 侧

```bash
# Worker 启动日志中应看到
# "gRPC channel established (TLS=on, mTLS=on)"
# "ControlService.Register OK lease_ttl_ms=30000"
```

### CN/SAN 绑定校验

故意伪造场景:用 `worker-A` 的证书 + metadata `x-worker-id: worker-B`:

```bash
# 预期 Gateway 拒绝并写入 audit:security
# event_type=mtls_reject reason="cn/san 'worker-A' does not match worker_id 'worker-B'"
redis-cli XREVRANGE audit:security + - COUNT 1
```

---

## 排错

| 现象 | 可能原因 |
|---|---|
| Worker 握手失败 `bad_certificate` | Worker 客户端证书不是 CA 签的 / CA 文件不对 |
| `PERMISSION_DENIED` `mtls_binding_failed` | 证书 CN/SAN 与 `worker_id` 不一致 |
| Gateway 启动报 `Cannot read TLS CA` | `GRPC_TLS_CA_PATH` 文件不存在 / 权限不够 |
| Worker 连不上但日志无错 | `endpoint` 是 IP,但证书 SAN 只有域名 |
| 证书快过期(7 天内) | 重新签发 + 滚动重启 Worker / Gateway |

---

## 证书轮换

```bash
# 提前 30 天重签 worker 证书
WORKER_ID=worker-prod-001
openssl x509 -req -in worker-${WORKER_ID}.csr \
  -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out worker-${WORKER_ID}-v2.crt -days 365 \
  -extfile <(printf "subjectAltName=DNS:${WORKER_ID}")

# 分发新证书到 Worker 主机,滚动重启
```

CA 证书有效期 10 年。CA 到期前 1 年需要规划新 CA + 交叉签名过渡。

---

## 相关代码

- [Gateway TLS 加载](../services/gateway/src/antcode_gateway/server.py)(行 145-170)
- [Gateway 配置](../services/gateway/src/antcode_gateway/config.py)(行 79-140)
- [mTLS CN/SAN 绑定校验](../services/gateway/src/antcode_gateway/auth.py)(`_check_mtls_binding`)
- [安全审计 Stream](../services/gateway/src/antcode_gateway/auth.py)(`_emit_audit`)
