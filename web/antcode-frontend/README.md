# AntCode 前端项目

基于 React 19 + TypeScript + Ant Design 的现代化任务调度管理前端应用。

## ✨ 核心特性

- 🎨 **现代化 UI** - Ant Design 5.x 组件库，支持深色/浅色主题
- 🧭 **Git 项目管理** - 管理仓库来源、任务和运行时环境
- 📊 **数据可视化** - Chart.js 图表展示任务执行统计
- 🔄 **实时更新** - SSE 实时推送日志与执行状态
- 🚀 **性能优化** - 代码分割、懒加载、Gzip 压缩
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

- **Node.js**: >= 22.22.0
- **npm**: >= 10.9.0

（`package.json` 的 `engines` 是权威值，低于它 `npm install` 会被拒。）

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
| `npm run build:analyze` | 生成 `dist/stats.html` 包体分析（不自动开浏览器） |
| `npm run test` / `test:watch` / `test:ci` | Vitest 单测 |
| `npm run lint:ci` | `--max-warnings 0`，Docker 构建门禁用 |
| `npm run build:staging` | staging 模式构建 |

---

## 📁 项目结构

```
antcode-frontend/
├── public/                 # 静态资源
├── src/                    # 源代码目录
│   ├── assets/             # 资源文件
│   ├── components/         # 可复用组件
│   │   ├── common/         # 通用组件（Layout, AuthGuard, ErrorBoundary）
│   │   ├── projects/       # 项目相关组件
│   │   ├── runtimes/       # 运行时相关组件
│   │   ├── ui/             # UI 组件（LogViewer 等）
│   │   └── workers/        # Worker 相关组件（含爬虫统计）
│   ├── config/             # 配置文件
│   ├── contexts/           # React Context
│   ├── hooks/              # 自定义 Hooks
│   │   └── api/            # React Query hooks
│   ├── lib/                # 第三方库配置
│   ├── pages/              # 页面组件
│   │   ├── AlertConfig/    # 告警配置
│   │   ├── AuditLog/       # 审计日志
│   │   ├── Crawl/          # 爬虫批次
│   │   ├── Dashboard/      # 仪表盘
│   │   ├── Envs/           # 环境管理
│   │   ├── Login/          # 登录页
│   │   ├── Monitor/        # 监控（无独立路由，Dashboard 内 lazy tab）
│   │   ├── Projects/       # 项目管理
│   │   ├── Repositories/   # Git 仓库管理
│   │   ├── Settings/       # 设置页面
│   │   ├── SystemConfig/   # 系统配置
│   │   ├── Tasks/          # 任务管理（含 ExecutionLogs 日志查看）
│   │   ├── UserManagement/ # 用户管理
│   │   └── Workers/        # Worker 管理
│   ├── services/           # API 服务
│   ├── stores/             # 状态管理（Zustand）
│   ├── styles/             # 全局样式
│   ├── test/               # 测试基建（FakeEventSource 等）
│   ├── types/              # TypeScript 类型定义
│   ├── utils/              # 工具函数
│   ├── App.tsx             # 应用根组件
│   └── main.tsx            # 应用入口
├── scripts/                # 构建辅助脚本（generate-bundle-report.mjs）
├── dist/                   # 构建产物（自动生成）
├── Dockerfile              # 多阶段构建 + nginx-unprivileged
├── eslint.config.js        # ESLint 配置
├── index.html              # HTML 入口
├── package.json            # 项目配置
├── tsconfig.json           # TS 根配置（project references）
├── tsconfig.app.json       # 应用 TS 配置（`npm run type-check` 实际检这个 + node）
├── tsconfig.node.json      # 构建脚本 TS 配置
├── vitest.config.ts        # Vitest 配置
└── vite.config.ts          # Vite 构建配置
```

---

## 🛠️ 技术栈

版本以 `package.json` 为准，下面只写声明值。

### 核心框架
- **React**: 19.2.7（精确锁版，不是 caret）
- **TypeScript**: ^5.6.3
- **Vite**: 7.3.6（精确锁版）

### UI 组件库
- **Ant Design**: ^5.21.6
- **@ant-design/icons**: ^5.5.1

### 状态管理与路由
- **Zustand**: ^5.0.0 - 轻量级状态管理
- **@tanstack/react-query**: ^5.66.1 - 数据获取与缓存
- **react-router**: 8.3.0 - 路由管理（**不是 `react-router-dom`**，仓库里没装它）

### 数据可视化
- **Chart.js**: ^4.5.1 + **chartjs-plugin-zoom** ^2.2.0
- **react-chartjs-2**: ^5.3.1

### HTTP 客户端
- **Axios**: ^1.7.7

### 工具库
- **dayjs**: ^1.11.13 / **date-fns**: ^4.1.0 - 日期处理
- **immer**: ^10.1.1 - 不可变数据
- **node-forge**: 1.4.0 - 登录口令加密（`src/utils/loginEncryption.ts`）
- **react-syntax-highlighter**: 16.1.1 - 代码高亮

### 开发工具
- **ESLint**: ^10.8.0 + **typescript-eslint** ^8.65.0
- **Prettier**: ^3.3.3
- **Vitest**: 4.1.10 + @testing-library + jsdom + fake-indexeddb
- **Terser**: ^5.36.0 - 代码压缩
- **vite-plugin-compression**: 0.5.1 - Gzip 压缩

---

## 💻 开发指南

### 开发环境配置

前端开发模式会读取仓库根目录 `.env` 中的 `BIND_HOST`、`SERVER_PORT` 和 `FRONTEND_PORT`。
通常不需要再单独配置 `VITE_API_BASE_URL`；只有前端需要访问独立 API 域名时才设置它作为显式覆盖。

被读取的 `VITE_*`（`vite.config.ts` + `src/config/app.ts` + `src/utils/constants.ts`）：

```env
VITE_API_BASE_URL=
VITE_APP_TITLE=AntCode 任务调度平台
VITE_APP_NAME=
VITE_APP_LOGO_TEXT=
VITE_APP_LOGO_ICON=
VITE_APP_LOGO_SHORT=
VITE_APP_LOGO_URL=
VITE_APP_FAVICON_URL=
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

`vite.config.ts` 的 `manualChunks` 切 5 个 vendor chunk（体积以 `npm run build:analyze` 实测为准）：

| chunk | 内容 |
|---|---|
| `core` | react / react-dom / react-router / zustand / immer |
| `antd` | antd |
| `icons` | @ant-design/icons |
| `charts` | chart.js / react-chartjs-2 / chartjs-plugin-zoom |
| `syntax` | react-syntax-highlighter |

#### ✅ 资源压缩
- **Terser** 代码压缩（移除注释、压缩变量名）
- **Gzip** 压缩（`vite-plugin-compression`，产出 `.gz`；**没有配 Brotli**）

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

**唯一权威模板在 `Dockerfile` 里**（`/etc/nginx/templates/default.conf.template`），
不要手抄一份到别处。自建配置时必须照搬这几条，否则会踩已实测过的坑：

- **`proxy_pass` 必须走变量 + `resolver 127.0.0.11 valid=10s`，绝不能写字面主机名。**
  nginx 对字面主机名只在加载配置时解析一次并永久缓存；`infra/docker/deploy-production.sh`
  重启 web-api 后 IPAM 会换地址，而 frontend 不在重启序列里 —— 结果是部署后 `/api`
  永久 502/504 且不自愈（真机实测三个容器地址互换）。
- `location /api` **不带**尾斜杠、`proxy_pass` 也不带路径，即不做路径重写。
- SSE 日志流单独一个 `location ~ ^/api/v1/logs/runs/[^/]+/stream$`，关 `proxy_buffering`
  与 `proxy_cache`、`proxy_read_timeout 3600s`（后端另有 15s ping 与
  `X-Accel-Buffering: no` 兜底）。
- 镜像是 nginx-unprivileged，**监听 8080 不是 80**。
- 六条安全头（CSP / HSTS / X-Frame-Options / X-Content-Type-Options /
  Referrer-Policy / Permissions-Policy）、`set_real_ip_from` + `real_ip_header`、
  `client_max_body_size 100m` 都在模板里，别漏。

### Docker 部署

`Dockerfile` 已存在，多阶段（node 构建 + nginx-unprivileged 运行，均 digest 锁定），
构建阶段内置 `type-check` + `lint:ci` + `build` 门禁：

```bash
docker build -t antcode-frontend .
docker run -d -p 80:8080 antcode-frontend   # 容器内监听 8080
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
- ✅ manualChunks 代码分割
- ✅ Gzip 压缩
- ✅ Tree-shaking
- ✅ Terser 代码压缩
- ✅ CSS 代码分割

#### 2. 加载优化
- ✅ 懒加载路由组件
- ✅ DNS 预解析（`index.html` 的 `dns-prefetch`）
- ✅ 静态资源 Hash 命名

#### 3. 运行时优化
- ✅ 使用 React.memo 减少重渲染
- ✅ 虚拟滚动长列表（`VirtualLogViewer`，阈值 100 行）

体积与首屏耗时不在此处写死；每次改完跑 `npm run build:analyze` 看 `dist/stats.html`。

---

## 🔍 常见问题

### Q1: 端口冲突怎么办？

改仓库根 `.env` 的 `FRONTEND_PORT`。`vite.config.ts` 的 `server.port` 是从这个 env
算出来的，直接改死值会被覆盖逻辑绕开。

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
node --version  # 应该 >= 22.22.0

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
nvm use 22

node --version  # 需 >= v22.22.0
npm --version   # 需 >= 10.9.0
```

### Q7: 如何分析包大小？

```bash
npm run build:analyze
```

产出 `dist/stats.html`（treemap），自己开浏览器打开——脚本传了 `--open false`，不会自动弹。

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
3. **类型安全**: `src/` 全 TypeScript，`strict: true`
4. **性能优化**: Gzip 压缩、缓存策略
5. **错误处理**: ErrorBoundary 优雅降级
6. **状态管理**: Zustand 轻量级状态管理

版本历史见仓库根 [`CHANGELOG.md`](../../CHANGELOG.md)。

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
