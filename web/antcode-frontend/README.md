# AntCode 前端项目

基于 React 18 + TypeScript + Ant Design 的现代化任务调度管理前端应用。

## ✨ 核心特性

- 🎨 **现代化 UI** - Ant Design 5.x 组件库，支持深色/浅色主题
- 🧭 **Git 项目管理** - 管理仓库来源、任务和运行时环境
- 📊 **数据可视化** - Chart.js 图表展示任务执行统计
- 🔄 **实时更新** - SSE 实时推送日志与执行状态
- 🚀 **性能优化** - 代码分割、懒加载、Gzip/Brotli 压缩
- 📱 **响应式设计** - 支持桌面端、平板、手机

## 📋 目录

- [快速开始](#快速开始)
- [项目结构](#项目结构)
- [技术栈](#技术栈)
- [开发指南](#开发指南)
- [构建部署](#构建部署)
- [性能优化](#性能优化)
- [常见问题](#常见问题)

---

## 🚀 快速开始

### 环境要求

- **Node.js**: >= 22.0.0
- **npm**: >= 10.0.0

### 安装与运行

```bash
# 1. 安装依赖
npm install

# 2. 启动开发服务器
npm run dev

# 3. 访问应用
open http://localhost:3000
```

### 可用命令

| 命令 | 说明 |
|------|------|
| `npm run dev` | 启动开发服务器 (端口 3000) |
| `npm run build` | 生产环境构建 |
| `npm run preview` | 预览生产构建 (端口 3001) |
| `npm run lint` | ESLint 代码检查 |
| `npm run lint:fix` | 自动修复代码问题 |
| `npm run type-check` | TypeScript 类型检查 |
| `npm run format` | Prettier 格式化代码 |
| `npm run clean` | 清理构建缓存 |
| `npm run build:analyze` | 分析构建产物大小 |

---

## 📁 项目结构

```
antcode-frontend/
├── public/                 # 静态资源
├── src/                    # 源代码目录
│   ├── assets/             # 资源文件
│   ├── components/         # 可复用组件
│   │   ├── common/         # 通用组件（Layout, AuthGuard, ErrorBoundary）
│   │   ├── runtimes/       # 运行时相关组件
│   │   ├── projects/       # 项目相关组件
│   │   ├── runtimes/       # 运行时相关组件
│   │   ├── ui/             # UI 组件（LogViewer 等）
│   │   └── workers/        # Worker 相关组件
│   ├── config/             # 配置文件
│   ├── contexts/           # React Context
│   ├── hooks/              # 自定义 Hooks
│   │   └── api/            # API 相关 Hooks
│   ├── lib/                # 第三方库配置
│   ├── pages/              # 页面组件
│   │   ├── AlertConfig/    # 告警配置
│   │   ├── AuditLog/       # 审计日志
│   │   ├── Dashboard/      # 仪表盘
│   │   ├── Envs/           # 环境管理
│   │   ├── Login/          # 登录页
│   │   ├── Logs/           # 日志查看
│   │   ├── Monitor/        # 监控页面
│   │   ├── Projects/       # 项目管理
│   │   ├── Repositories/   # Git 仓库管理
│   │   ├── Settings/       # 设置页面
│   │   ├── SpiderMonitor/  # 爬虫监控
│   │   ├── SystemConfig/   # 系统配置
│   │   ├── Tasks/          # 任务管理
│   │   ├── UserManagement/ # 用户管理
│   │   └── Workers/        # Worker 管理
│   ├── services/           # API 服务
│   ├── stores/             # 状态管理（Zustand）
│   ├── styles/             # 全局样式
│   ├── types/              # TypeScript 类型定义
│   ├── utils/              # 工具函数
│   ├── App.tsx             # 应用根组件
│   └── main.tsx            # 应用入口
├── dist/                   # 构建产物（自动生成）
├── eslint.config.js        # ESLint 配置
├── index.html              # HTML 入口
├── package.json            # 项目配置
├── tsconfig.json           # TypeScript 配置
└── vite.config.ts          # Vite 构建配置
```

---

## 🛠️ 技术栈

### 核心框架
- **React**: 18.3.1 - UI 框架
- **TypeScript**: 5.6.3 - 类型安全
- **Vite**: 5.4.10 - 构建工具

### UI 组件库
- **Ant Design**: 5.21.6 - UI 组件库
- **@ant-design/icons**: 5.5.1 - 图标库

### 状态管理与路由
- **Zustand**: 5.0.0 - 轻量级状态管理
- **@tanstack/react-query**: 5.x - 数据获取与缓存
- **React Router**: 6.27.0 - 路由管理

### 数据可视化
- **Chart.js**: 4.5.1 - 图表库
- **react-chartjs-2**: 5.3.1 - React 图表封装

### HTTP 客户端
- **Axios**: 1.7.7 - HTTP 请求库

### 工具库
- **dayjs**: 1.11.13 - 日期处理
- **immer**: 10.1.1 - 不可变数据

### 开发工具
- **ESLint**: 9.13.0 - 代码检查
- **Prettier**: 3.3.3 - 代码格式化
- **TypeScript ESLint**: 8.11.0 - TypeScript 规则
- **Terser**: 5.36.0 - 代码压缩
- **vite-plugin-compression**: 0.5.1 - Gzip/Brotli 压缩

---

## 💻 开发指南

### 开发环境配置

前端开发模式会读取仓库根目录 `.env` 中的 `BIND_HOST`、`SERVER_PORT` 和 `FRONTEND_PORT`。
通常不需要再单独配置 `VITE_API_BASE_URL`；只有前端需要访问独立 API 域名时才设置它作为显式覆盖。

```env
VITE_APP_TITLE=AntCode 任务调度平台
VITE_DEV_MODE=true
```

### 代码规范

#### 1. TypeScript 规范
- 使用严格模式 (`strict: true`)
- 为所有函数参数和返回值添加类型
- 避免使用 `any`，使用 `unknown` 替代
- 使用接口 (`interface`) 定义对象类型

#### 2. 数据访问约定
- HTTP 调用统一走 `src/services/api.ts` 封装的 axios 实例。
- 页面侧优先使用 `src/hooks/api/*` 中的 React Query hooks 获取/变更数据，并通过 `invalidateQueries` 触发刷新。
- 避免在页面直接手写 axios/fetch，请复用领域服务与 hooks。

#### 2. React 规范
- 使用函数组件 + Hooks
- 使用 `React.memo` 优化不必要的重渲染
- 合理使用 `useCallback` 和 `useMemo`
- 组件文件使用 PascalCase 命名

#### 3. CSS 规范
- 优先使用 CSS Modules (`.module.css`)
- 使用 CSS 变量进行主题定制
- 遵循 BEM 命名规范 (Block-Element-Modifier)

#### 4. 提交规范

遵循 Conventional Commits 规范:

```bash
feat: 新功能
fix: 修复 bug
docs: 文档更新
style: 代码格式调整
refactor: 重构
perf: 性能优化
test: 测试
chore: 构建/工具链更新
```

### 目录组织原则

1. **按功能模块组织**：每个页面/功能模块独立目录
2. **组件分层**：
   - `common/`: 通用组件 (如 Layout, AuthGuard)
   - `ui/`: 纯 UI 组件 (如 LogViewer)
   - `projects/`: 业务组件 (与项目相关)
3. **样式管理**：
   - 全局样式放在 `styles/`
   - 组件样式使用 CSS Modules
4. **类型定义**：统一放在 `types/` 目录
5. **工具函数**：统一放在 `utils/` 目录

---

## 🏗️ 构建部署

### 生产环境构建

```bash
# 1. 类型检查
npm run type-check

# 2. 代码检查
npm run lint

# 3. 生产构建
npm run build

# 4. 预览构建结果
npm run preview
```

构建产物将生成在 `dist/` 目录，包含:
- `index.html` - 入口 HTML
- `js/` - JavaScript 文件
- `css/` - CSS 文件
- `images/` - 图片资源

### 构建优化特性

#### ✅ 代码分割
- React 核心库独立打包 (~173KB)
- Ant Design 组件库独立打包 (~724KB)
- Chart.js 图表库独立打包 (~165KB)
- 工具库独立打包 (~62KB)

#### ✅ 资源压缩
- **Terser** 代码压缩（移除注释、压缩变量名）
- **Gzip** 压缩（压缩率 ~65-70%）
- **Brotli** 压缩（压缩率 ~70-75%）

#### ✅ 性能优化
- Tree-shaking 移除未使用代码
- 懒加载路由组件
- 静态资源 Hash 命名
- 生产环境移除 console.log
- CSS 代码分割

### Nginx 部署

#### 1. 基础配置

将构建产物复制到 Nginx 静态文件目录:

```bash
cp -r dist/* /usr/share/nginx/html/
```

#### 2. Nginx 配置

参考配置（与 `Dockerfile` 内置 nginx 模板一致）:

```nginx
server {
    listen 80;
    server_name your-domain.com;
    root /usr/share/nginx/html;
    index index.html;

    # 启用 Gzip 压缩
    gzip on;
    gzip_vary on;
    gzip_min_length 1024;
    gzip_types text/plain text/css text/xml text/javascript application/javascript application/json;

    # 静态资源缓存策略
    location ~* \.(js|css|png|jpg|jpeg|gif|svg|ico|woff|woff2|ttf|eot)$ {
        expires 1y;
        add_header Cache-Control "public, immutable";
    }

    # HTML 不缓存
    location ~* \.html$ {
        expires -1;
        add_header Cache-Control "no-store, no-cache, must-revalidate";
    }

    # SSE 实时日志流：必须关闭缓冲并放宽读超时，否则事件被攒批、
    # 静默流 60s 被掐断（后端 15s ping + X-Accel-Buffering: no 兜底）
    location ~ ^/api/v1/logs/runs/[^/]+/stream$ {
        proxy_pass http://web-api:8000;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }

    # API 反向代理
    location /api/ {
        proxy_pass http://web-api:8000/api/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    # SPA 路由支持
    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

#### 3. 应用配置

```bash
# 测试配置
nginx -t

# 重载配置
nginx -s reload
```

### Docker 部署

创建 `Dockerfile`:

```dockerfile
FROM nginx:alpine

# 复制构建产物
COPY dist/ /usr/share/nginx/html/

# 复制 Nginx 配置
COPY nginx.conf.example /etc/nginx/conf.d/default.conf

# 暴露端口
EXPOSE 80

# 启动 Nginx
CMD ["nginx", "-g", "daemon off;"]
```

构建并运行:

```bash
# 构建镜像
docker build -t antcode-frontend .

# 运行容器
docker run -d -p 80:80 antcode-frontend
```

### 环境变量配置

生产环境创建 `.env.production`:

```env
VITE_API_BASE_URL=/api
VITE_APP_TITLE=AntCode 任务调度平台
VITE_DEV_MODE=false
VITE_LOG_LEVEL=error
```

---

## ⚡ 性能优化

### 已实施的优化

#### 1. 构建优化
- ✅ 智能代码分割
- ✅ Gzip + Brotli 双重压缩
- ✅ Tree-shaking
- ✅ Terser 代码压缩
- ✅ CSS 代码分割

#### 2. 加载优化
- ✅ 懒加载路由组件
- ✅ DNS 预解析
- ✅ 预连接 API 服务器
- ✅ 静态资源 Hash 命名

#### 3. 运行时优化
- ✅ Ant Design 动画时长优化（减少 50%）
- ✅ 使用 React.memo 减少重渲染
- ✅ 虚拟滚动优化长列表

### 性能指标

#### 构建性能
```
构建时间：~6 秒
构建产物：6.1 MB（未压缩）
压缩后总大小：~2.5 MB（Gzip）/ ~2.0 MB（Brotli）
```

#### 包大小分析（Brotli 压缩后）
```
主应用包：      110 KB → 23 KB (-79%)
React 核心：    173 KB → 47 KB (-73%)
Ant Design：    724 KB → 161 KB (-78%)
Chart.js：      165 KB → 48 KB (-71%)
工具库：        62 KB → 21 KB (-66%)

总计首屏：     ~1.2 MB → ~320 KB (-73%)
```

#### 首屏加载（4G 网络）
```
FCP (First Contentful Paint): < 1.5s
LCP (Largest Contentful Paint): < 2.5s
TTI (Time to Interactive): < 3.5s
```

### 持续优化建议

#### 短期优化
1. 实施虚拟滚动优化长列表
2. 添加 Service Worker 支持离线访问
3. 图片懒加载和 WebP 格式
4. 实施资源预加载策略

#### 长期优化
1. 考虑 SSR/SSG 提升首屏性能
2. 实施微前端架构（如需要）
3. 探索 Rust 工具链（如 SWC）
4. 升级到 React 19（正式版发布后）

---

## 🔍 常见问题

### Q1: 端口冲突怎么办？

修改 `vite.config.ts` 中的 `server.port`:

```typescript
server: {
  port: 3001, // 改为其他端口
}
```

### Q2: API 请求失败？

检查后端服务是否启动在仓库根目录 `.env` 的 `SERVER_PORT` 端口，或修改代理配置。

### Q3: 热更新不工作？

```bash
# 清理缓存重启
npm run clean
npm install
npm run dev
```

### Q4: 构建失败？

```bash
# 检查 Node.js 版本
node --version  # 应该 >= 22.0.0

# 重新安装依赖
rm -rf node_modules package-lock.json
npm install
```

### Q5: ESLint 警告太多？

项目允许最多 50 个警告。当前的警告主要是 `any` 类型使用，不影响功能。

可以通过定义更详细的类型接口来消除这些警告。

### Q6: 如何切换 Node.js 版本？

如果使用 nvm:

```bash
# 切换到 Node.js 22
nvm use v22.13.0

# 验证版本
node --version  # 应该显示 v22.13.0
npm --version   # 应该显示 >= v10.0.0
```

### Q7: 如何分析包大小？

```bash
# 构建并生成可视化报告
npm run build:analyze
```

这将在浏览器中打开一个交互式的包大小分析图。

### Q8: 主题切换不工作？

检查 `ThemeContext` 是否正确初始化，确保所有页面都被 `ThemeProvider` 包裹。

---

## 📊 项目特性

### 核心功能

- ✅ **响应式设计**: 支持桌面端、平板、手机
- ✅ **主题切换**: 浅色/深色主题
- ✅ **项目管理**: 创建、编辑、删除项目
- ✅ **任务管理**: 任务执行和监控
- ✅ **实时日志**: SSE 实时日志显示
- ✅ **用户认证**: JWT 登录认证
- ✅ **权限控制**: 基于角色的权限管理
- ✅ **Git 项目管理**: 仓库来源、项目配置和任务运行管理
- ✅ **数据可视化**: Chart.js 图表展示

### 技术亮点

1. **代码分割**: 减少首屏加载时间
2. **懒加载**: 按需加载路由和组件
3. **类型安全**: 100% TypeScript 覆盖
4. **性能优化**: Gzip/Brotli 压缩、缓存策略
5. **错误处理**: ErrorBoundary 优雅降级
6. **状态管理**: Zustand 轻量级状态管理

---

## 📝 更新日志

### v1.0.0 (2026-01-15)

#### 优化
- ✅ 升级到 Node.js 22.13.0
- ✅ 修复所有 ESLint 错误
- ✅ 实施代码分割和懒加载
- ✅ 添加 Gzip/Brotli 压缩
- ✅ 优化构建配置
- ✅ 改进 Nginx 配置

#### 新增
- ✅ 环境管理页面
- ✅ 监控页面
- ✅ Git 仓库来源管理
- ✅ 主题切换功能
- ✅ 节点环境选择器

---

## 🤝 贡献指南

1. Fork 项目
2. 创建功能分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'feat: Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 创建 Pull Request

---

## 📄 许可证

MIT License

---

## 📞 联系方式

如有问题或建议，请提交 Issue 或 Pull Request。

---

**祝开发愉快！** 🚀
