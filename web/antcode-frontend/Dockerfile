# =====================================================
# AntCode Frontend - 多阶段构建 Dockerfile
# =====================================================

# ================== 阶段 1: 构建阶段 ==================
FROM node:22-alpine as builder

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV NODE_ENV=production \
    NPM_CONFIG_LOGLEVEL=warn

# 复制package文件
COPY package*.json ./

# 安装依赖
RUN npm ci --only=production --ignore-scripts && \
    npm cache clean --force

# 复制源代码
COPY . .

# 构建应用
RUN npm run build

# ================== 阶段 2: 运行时镜像 ==================
FROM nginx:1.27-alpine as runtime

# 设置工作目录
WORKDIR /usr/share/nginx/html

# 删除默认的nginx静态资源
RUN rm -rf ./*

# 从构建阶段复制构建产物
COPY --from=builder /app/dist .

# 复制nginx配置
COPY nginx.conf /etc/nginx/conf.d/default.conf

# 添加健康检查
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget --quiet --tries=1 --spider http://localhost:80/ || exit 1

# 暴露端口
EXPOSE 80

# 启动nginx
CMD ["nginx", "-g", "daemon off;"]

