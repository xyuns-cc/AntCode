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
#   - NGINX_ENVSUBST_FILTER 用正则精确锁死只替换 WEB_API_UPSTREAM,避免误伤
#     $http_upgrade / $host / $remote_addr 等 nginx 自身变量。
ENV WEB_API_UPSTREAM=web-api:8000
# 正则不加锚点(避免 Dockerfile 对末尾 $ 的解释),现网仅此一个 WEB_API_UPSTREAM 命名的
# 环境变量,不会误匹配其它。envsubst 只对这个变量做替换,$http_upgrade / $host 保持原样。
ENV NGINX_ENVSUBST_FILTER=WEB_API_UPSTREAM

# Nginx 配置（unprivileged 监听 8080 而非 80）
# SSE 日志流（/api/v1/logs/runs/{id}/stream）需要独立 location：关闭 proxy_buffering
# （默认 on 会把事件攒批推送，"实时"变成成批延迟）并拉长 read timeout（默认 60s 会
# 掐断静默流；后端另有 15s ping 事件与 X-Accel-Buffering: no 响应头兜底）。
RUN mkdir -p /etc/nginx/templates && echo 'server { \
    listen 8080; \
    server_name localhost; \
    root /usr/share/nginx/html; \
    index index.html; \
    # P2-01: SPA 层安全头 (Web API 已由 SecurityHeadersMiddleware 覆盖 /api 响应, \
    # 这里补齐 nginx 直出的 HTML/JS 资源, 避免 XSS/clickjacking/HTTPS 降级). \
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
        proxy_pass http://${WEB_API_UPSTREAM}; \
        proxy_http_version 1.1; \
        proxy_set_header Connection ""; \
        proxy_set_header Host $host; \
        proxy_set_header X-Real-IP $remote_addr; \
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for; \
        proxy_set_header X-Forwarded-Proto $scheme; \
        proxy_buffering off; \
        proxy_cache off; \
        proxy_read_timeout 3600s; \
        proxy_send_timeout 3600s; \
    } \
    location /api { \
        proxy_pass http://${WEB_API_UPSTREAM}; \
        proxy_set_header Host $host; \
        proxy_set_header X-Real-IP $remote_addr; \
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for; \
        proxy_set_header X-Forwarded-Proto $scheme; \
    } \
    location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2)$ { \
        expires 1y; \
    } \
    gzip on; \
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml; \
}' > /etc/nginx/templates/default.conf.template

# 健康检查（unprivileged nginx 走 8080）
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget --quiet --tries=1 --spider http://127.0.0.1:8080/ || exit 1

EXPOSE 8080

# 切回非 root 运行（J4）
USER nginx

CMD ["nginx", "-g", "daemon off;"]
