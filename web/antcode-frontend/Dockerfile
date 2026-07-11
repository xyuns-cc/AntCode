# AntCode Frontend - Multi-stage Build Dockerfile

# Stage 1: Build
FROM node:22-alpine AS builder

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
FROM nginxinc/nginx-unprivileged:1.27-alpine AS runtime

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
# P1-06: /api/v1/ws/ 独立 location 补齐 WebSocket Upgrade 头,避免落进普通 /api 走 HTTP/1.0
RUN mkdir -p /etc/nginx/templates && echo 'server { \
    listen 8080; \
    server_name localhost; \
    root /usr/share/nginx/html; \
    index index.html; \
    location / { \
        try_files $uri $uri/ /index.html; \
    } \
    location /api/v1/ws/ { \
        proxy_pass http://${WEB_API_UPSTREAM}; \
        proxy_http_version 1.1; \
        proxy_set_header Upgrade $http_upgrade; \
        proxy_set_header Connection "upgrade"; \
        proxy_set_header Host $host; \
        proxy_set_header X-Real-IP $remote_addr; \
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for; \
        proxy_set_header X-Forwarded-Proto $scheme; \
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
    location /ws { \
        proxy_pass http://${WEB_API_UPSTREAM}; \
        proxy_http_version 1.1; \
        proxy_set_header Upgrade $http_upgrade; \
        proxy_set_header Connection "upgrade"; \
        proxy_set_header Host $host; \
        proxy_set_header X-Real-IP $remote_addr; \
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for; \
        proxy_read_timeout 3600s; \
        proxy_send_timeout 3600s; \
    } \
    location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2)$ { \
        expires 1y; \
        add_header Cache-Control "public, immutable"; \
    } \
    gzip on; \
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml; \
}' > /etc/nginx/templates/default.conf.template

# 健康检查（unprivileged nginx 走 8080）
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget --quiet --tries=1 --spider http://localhost:8080/ || exit 1

EXPOSE 8080

# 切回非 root 运行（J4）
USER nginx

CMD ["nginx", "-g", "daemon off;"]
