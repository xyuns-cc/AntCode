# AntCode Frontend - Multi-stage Build Dockerfile

# Stage 1: Build
FROM node:22-alpine as builder
WORKDIR /app
ENV NODE_ENV=production \
    NPM_CONFIG_LOGLEVEL=warn
COPY package*.json ./
RUN npm ci --only=production --ignore-scripts && npm cache clean --force
COPY . .
RUN npm run build

# Stage 2: Production
FROM nginx:1.27-alpine as runtime
WORKDIR /usr/share/nginx/html
RUN rm -rf ./*

# Copy build output
COPY --from=builder /app/dist .

# Inline nginx configuration for SPA
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
    location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2)$ { \
        expires 1y; \
        add_header Cache-Control "public, immutable"; \
    } \
    gzip on; \
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml; \
}' > /etc/nginx/conf.d/default.conf

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget --quiet --tries=1 --spider http://localhost:80/ || exit 1

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
