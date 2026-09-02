# 🚪 AntCode Gateway (数据面)

Gateway 是连接公网与内网的桥梁。它为部署在公网或跨云环境的 Worker 提供了一个统一、安全、高性能的 gRPC 接入层。

---

## 🛡️ 核心职责

1.  **安全接入 (Secure Access)**: 作为公网入口，所有请求必须通过 API Key 或 mTLS 双向认证。
2.  **协议转换 (Protocol Translation)**: 将 Worker 的 gRPC 请求转换为内部 Redis Stream 消息指令，对后端透明。
3.  **连接管理 (Connection Mgmt)**: 维护长连接，处理心跳保活，识别并剔除失联 Worker。
4.  **流量控制 (Rate Limiting)**: 防止恶意或失控的 Worker 流量打垮后端存储。

---

## ⚡ 快速启动

### 命令行启动

```bash
uv run python -m antcode_gateway
```

### 推荐配置

生产环境建议通过环境变量进行配置：

| 变量名 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `GRPC_HOST` | `0.0.0.0` | 监听地址 |
| `GRPC_PORT` | `50051` | 监听端口 |
| `AUTH_ENABLED` | `true` | 是否开启鉴权 (生产环境必须开启) |
| `RATE_LIMIT_ENABLED` | `true` | 是否开启限流 |

---

## 🔒 安全配置建议

TLS 没有独立开关，配了证书路径就启用：

```bash
export GRPC_TLS_CERT_PATH=/path/to/server.crt
export GRPC_TLS_KEY_PATH=/path/to/server.key
```

再补 CA 即为 mTLS（校验客户端证书，CN/SAN 须匹配 worker_id）：

```bash
export GRPC_TLS_CA_PATH=/path/to/ca.crt
```

`AUTH_ENABLED=true` 但没有 TLS 证书时网关拒绝启动（凭证会明文传输），
仅本地测试可用 `ANTCODE_GATEWAY_ALLOW_INSECURE=true` 显式放行。
