# AntCode Frontend - Multi-stage Build Dockerfile

# Stage 1: Build
FROM node:22-alpine@sha256:16e22a550f3863206a3f701448c45f7912c6896a62de43add43bb9c86130c3e2 AS builder

WORKDIR /app

# 设置环境变量，增加 Node.js 内存限制
ENV NODE_OPTIONS="--max-old-space-size=4096"

# 复制依赖文件
COPY package*.json ./

# 安装所有依赖
RUN npm ci

# 复制源代码
COPY . .

# 列出文件确认复制成功
RUN ls -la && ls -la src/

# 镜像构建必须经过与 CI 一致的静态门禁。
RUN npm run type-check && npm run lint:ci && npm run build

# Stage 2: Production (J4: nginx unprivileged 镜像以非 root 跑)
FROM nginxinc/nginx-unprivileged:1.27-alpine@sha256:65e3e85dbaed8ba248841d9d58a899b6197106c23cb0ff1a132b7bfe0547e4c0 AS runtime

# unprivileged 镜像默认 uid=101 (nginx)，监听 8080 而不是 80
USER root

WORKDIR /usr/share/nginx/html

# 删除默认内容
RUN rm -rf ./*

# 从构建阶段复制产物
COPY --from=builder /app/dist .
RUN chown -R nginx:nginx /usr/share/nginx/html

# P1-06: Nginx upstream 从环境变量读取（默认 web-api:8000,对齐 docker-compose 服务名）
#   - 通过 /etc/nginx/templates/*.template 让 nginxinc 入口脚本在启动时用 envsubst
#     生成 /etc/nginx/conf.d/*.conf,支持在部署时用 WEB_API_UPSTREAM 覆盖后端地址。
#   - NGINX_ENVSUBST_FILTER 只替换 upstream 与受信 edge proxy 地址，避免误伤
#     $http_upgrade / $host / $remote_addr 等 Nginx 自身变量。
ENV WEB_API_UPSTREAM=web-api:8000
ENV TRUSTED_EDGE_PROXY_IP=127.0.0.1
# 正则不加锚点（避免 Dockerfile 对末尾 $ 的解释）；两个变量名均为部署专用前缀，
# 不会匹配 Nginx 运行时变量，$http_upgrade / $host 等会保持原样。
ENV NGINX_ENVSUBST_FILTER=WEB_API_UPSTREAM|TRUSTED_EDGE_PROXY_IP

# Nginx 配置（unprivileged 监听 8080 而非 80）
# SSE 日志流（/api/v1/logs/runs/{id}/stream）需要独立 location：关闭 proxy_buffering
# （默认 on 会把事件攒批推送，"实时"变成成批延迟）并拉长 read timeout（默认 60s 会
# 掐断静默流；后端另有 15s ping 事件与 X-Accel-Buffering: no 响应头兜底）。
# P2-01: add_header 安全头系列补齐 nginx 直出的 HTML/JS 资源（Web API 已由
# SecurityHeadersMiddleware 覆盖 /api 响应），避免 XSS/clickjacking/HTTPS 降级。
#
# proxy_pass 必须走变量 + resolver，不能写字面主机名：nginx 对字面主机名只在
# 加载配置时解析一次并永久缓存。deploy-production.sh 会 stop 再 up web-api，
# Docker 的 IPAM 按 lowest-free-first 重新分配地址，frontend 不在重启序列里
# （镜像 digest 不变就不重建），于是它会一直把 /api 打到一个已经属于别的容器
# 的旧地址——部署后 /api 永久 502/504 且不自愈（真机实测三个容器地址互换）。
# 变量形式让 nginx 每次请求都查 Docker 内嵌 DNS（127.0.0.11，所有用户自定义
# 网络恒定提供），valid=10s 限定缓存窗口。
#
# 警告：echo 块内绝不能写 # 注释——Docker 续行会把模板拼成单个物理行，
# nginx 的 # 注释直达行尾，会把其后全部配置（含闭合大括号）吞掉导致启动失败。
RUN mkdir -p /etc/nginx/templates && echo 'resolver 127.0.0.11 valid=10s ipv6=off; \
server { \
    listen 8080; \
    server_name localhost; \
    set $antcode_web_api ${WEB_API_UPSTREAM}; \
    set_real_ip_from ${TRUSTED_EDGE_PROXY_IP}; \
    real_ip_header X-Forwarded-For; \
    real_ip_recursive on; \
    client_max_body_size 100m; \
    root /usr/share/nginx/html; \
    index index.html; \
    add_header Content-Security-Policy "default-src '\''self'\''; script-src '\''self'\''; style-src '\''self'\'' '\''unsafe-inline'\''; img-src '\''self'\'' data: blob:; font-src '\''self'\'' data:; connect-src '\''self'\''; object-src '\''none'\''; base-uri '\''self'\''; frame-ancestors '\''none'\''; form-action '\''self'\''" always; \
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always; \
    add_header X-Frame-Options "DENY" always; \
    add_header X-Content-Type-Options "nosniff" always; \
    add_header Referrer-Policy "strict-origin-when-cross-origin" always; \
    add_header Permissions-Policy "geolocation=(), microphone=(), camera=()" always; \
    location / { \
        try_files $uri $uri/ /index.html; \
    } \
    location ~ ^/api/v1/logs/runs/[^/]+/stream$ { \
        proxy_pass http://$antcode_web_api; \
        proxy_http_version 1.1; \
        proxy_set_header Connection ""; \
        proxy_set_header Host $host; \
        proxy_set_header X-Real-IP $remote_addr; \
        proxy_set_header X-Forwarded-For $remote_addr; \
        proxy_set_header X-Forwarded-Proto $scheme; \
        proxy_buffering off; \
        proxy_cache off; \
        proxy_read_timeout 3600s; \
        proxy_send_timeout 3600s; \
    } \
    location /api { \
        proxy_pass http://$antcode_web_api; \
        proxy_set_header Host $host; \
        proxy_set_header X-Real-IP $remote_addr; \
        proxy_set_header X-Forwarded-For $remote_addr; \
        proxy_set_header X-Forwarded-Proto $scheme; \
    } \
    location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2)$ { \
        expires 1y; \
    } \
    gzip on; \
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml; \
}' > /etc/nginx/templates/default.conf.template

# 健康检查（unprivileged nginx 走 8080）：本地静态资源 + 穿透到 Web API 的
# /api/v1/health。只探静态页会在上游不可达时依然报 healthy，`up -d --wait`
# 就会在 /api 全挂的情况下宣告部署成功。
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -q -T 3 -O /dev/null http://127.0.0.1:8080/ \
        && wget -q -T 3 -O /dev/null http://127.0.0.1:8080/api/v1/health

EXPOSE 8080

# 切回非 root 运行（J4）
USER nginx

CMD ["nginx", "-g", "daemon off;"]
