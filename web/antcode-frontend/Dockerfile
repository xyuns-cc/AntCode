# AntCode Frontend - Multi-stage Build Dockerfile

# Stage 1: Build
FROM node:22-alpine AS builder

WORKDIR /app

# 设置环境变量，增加 Node.js 内存限制
ENV NODE_ENV=development \
    NPM_CONFIG_LOGLEVEL=warn \
    NODE_OPTIONS="--max-old-space-size=4096"

# 复制依赖文件
COPY package*.json ./

# 安装所有依赖（包括 devDependencies 用于构建）
RUN npm ci && npm cache clean --force

# 复制源代码
COPY . .

# 直接使用 vite build，跳过 type-check（CI 中单独检查）
ENV NODE_ENV=production
RUN npx vite build --mode production

# Stage 2: Production
FROM nginx:1.27-alpine AS runtime

WORKDIR /usr/share/nginx/html

# 删除默认内容
RUN rm -rf ./*

# 从构建阶段复制产物
COPY --from=builder /app/dist .

# Nginx 配置
RUN echo 'server { \
    listen 80; \
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

# 健康检查
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget --quiet --tries=1 --spider http://localhost:80/ || exit 1

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
