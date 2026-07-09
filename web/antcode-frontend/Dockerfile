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

# 构建（跳过 type-check，直接用 vite）
RUN ./node_modules/.bin/vite build --mode production

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

# Nginx 配置（unprivileged 监听 8080 而非 80）
RUN echo 'server { \
    listen 8080; \
    server_name localhost; \
    root /usr/share/nginx/html; \
    index index.html; \
    location / { \
        try_files $uri $uri/ /index.html; \
    } \
    location /api { \
        proxy_pass http://backend:8000; \
        proxy_set_header Host $host; \
        proxy_set_header X-Real-IP $remote_addr; \
    } \
    location /ws { \
        proxy_pass http://backend:8000; \
        proxy_http_version 1.1; \
        proxy_set_header Upgrade $http_upgrade; \
        proxy_set_header Connection "upgrade"; \
    } \
    location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2)$ { \
        expires 1y; \
        add_header Cache-Control "public, immutable"; \
    } \
    gzip on; \
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml; \
}' > /etc/nginx/conf.d/default.conf

# 健康检查（unprivileged nginx 走 8080）
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget --quiet --tries=1 --spider http://localhost:8080/ || exit 1

EXPOSE 8080

# 切回非 root 运行（J4）
USER nginx

CMD ["nginx", "-g", "daemon off;"]
