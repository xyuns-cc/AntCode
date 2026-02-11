# 用户管理 API 文档

## 概述

用户管理API提供了完整的用户账户管理功能，包括用户创建、查询、更新、删除和密码管理。系统采用基于角色的权限控制，区分管理员和普通用户权限。

## 基础路径

所有用户管理API的基础路径为：`/api/v1/users`

## 认证

所有API接口都需要JWT认证，在请求头中携带：
```
Authorization: Bearer <your-jwt-token>
```

## 响应格式

所有API响应都遵循统一格式：
```json
{
    "success": true,
    "code": 200,
    "message": "操作成功",
    "data": { ... }
}
```

## 权限说明

### 用户角色
- **管理员 (is_admin: true)**: 拥有所有用户管理权限
- **普通用户 (is_admin: false)**: 只能操作自己的账户信息

### 权限矩阵
| 操作 | 管理员 | 普通用户 |
|------|--------|----------|
| 查看用户列表 | ✅ | ❌ |
| 创建用户 | ✅ | ❌ |
| 查看他人详情 | ✅ | ❌ |
| 查看自己详情 | ✅ | ✅ |
| 修改他人信息 | ✅ | ❌ |
| 修改自己信息 | ✅ | ✅（限制） |
| 重置他人密码 | ✅ | ❌ |
| 修改自己密码 | ✅ | ✅ |
| 删除用户 | ✅ | ❌ |

---

## API 接口

### 1. 获取用户列表

**GET** `/api/v1/users/`

获取所有用户列表，支持分页和筛选（仅管理员可访问）。

#### 查询参数
- `page` (int, 可选): 页码，默认1
- `size` (int, 可选): 每页数量，默认20，最大100
- `is_active` (boolean, 可选): 激活状态筛选
- `is_admin` (boolean, 可选): 管理员状态筛选

#### 请求示例
```bash
curl -X GET "/api/v1/users/?page=1&size=10&is_active=true" \
  -H "Authorization: Bearer <admin-token>"
```

#### 响应示例
```json
{
    "data": [
        {
            "id": 1,
            "username": "admin",
            "email": "admin@example.com",
            "is_active": true,
            "is_admin": true,
            "created_at": "2024-01-01T00:00:00Z",
            "last_login_at": "2024-01-01T10:00:00Z"
        },
        {
            "id": 2,
            "username": "user01",
            "email": "user01@example.com",
            "is_active": true,
            "is_admin": false,
            "created_at": "2024-01-01T01:00:00Z",
            "last_login_at": "2024-01-01T09:30:00Z"
        }
    ],
    "pagination": {
        "page": 1,
        "size": 10,
        "total": 25,
        "pages": 3
    }
}
```

---

### 2. 创建用户

**POST** `/api/v1/users/`

创建新用户账户（仅管理员可访问）。

#### 请求体
```json
{
    "username": "newuser",
    "password": "securepassword123",
    "email": "newuser@example.com",
    "is_admin": false
}
```

#### 请求参数说明
- `username` (string, 必需): 用户名，3-20字符，唯一
- `password` (string, 必需): 密码，最少8字符
- `email` (string, 可选): 邮箱地址，必须唯一
- `is_admin` (boolean, 可选): 是否为管理员，默认false

#### 请求示例
```bash
curl -X POST /api/v1/users/ \
  -H "Authorization: Bearer <admin-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "newuser",
    "password": "securepassword123",
    "email": "newuser@example.com",
    "is_admin": false
  }'
```

#### 响应示例
```json
{
    "success": true,
    "code": 201,
    "message": "用户创建成功",
    "data": {
        "id": 3,
        "username": "newuser",
        "email": "newuser@example.com",
        "is_active": true,
        "is_admin": false,
        "created_at": "2024-01-01T12:00:00Z",
        "last_login_at": null
    }
}
```

---

### 3. 获取用户详情

**GET** `/api/v1/users/{user_id}`

获取指定用户的详细信息。

#### 路径参数
- `user_id` (int, 必需): 用户ID

#### 权限说明
- 管理员：可查看任何用户的详情
- 普通用户：只能查看自己的详情

#### 请求示例
```bash
# 管理员查看其他用户
curl -X GET /api/v1/users/2 \
  -H "Authorization: Bearer <admin-token>"

# 普通用户查看自己
curl -X GET /api/v1/users/2 \
  -H "Authorization: Bearer <user-token>"
```

#### 响应示例
```json
{
    "success": true,
    "code": 200,
    "message": "查询成功",
    "data": {
        "id": 2,
        "username": "user01",
        "email": "user01@example.com",
        "is_active": true,
        "is_admin": false,
        "created_at": "2024-01-01T01:00:00Z",
        "last_login_at": "2024-01-01T09:30:00Z"
    }
}
```

---

### 4. 更新用户信息

**PUT** `/api/v1/users/{user_id}`

更新用户基本信息。

#### 路径参数
- `user_id` (int, 必需): 用户ID

#### 请求体
```json
{
    "email": "newemail@example.com",
    "is_active": true,
    "is_admin": false
}
```

#### 请求参数说明
- `email` (string, 可选): 新邮箱地址
- `is_active` (boolean, 可选): 激活状态（仅管理员可修改）
- `is_admin` (boolean, 可选): 管理员权限（仅管理员可修改）

#### 权限限制
- 管理员：可修改所有字段
- 普通用户：只能修改自己的邮箱，不能修改 `is_active` 和 `is_admin`

#### 请求示例
```bash
# 管理员更新用户状态
curl -X PUT /api/v1/users/2 \
  -H "Authorization: Bearer <admin-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "updated@example.com",
    "is_active": false
  }'

# 普通用户更新自己的邮箱
curl -X PUT /api/v1/users/2 \
  -H "Authorization: Bearer <user-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "mynewemail@example.com"
  }'
```

#### 响应示例
```json
{
    "success": true,
    "code": 200,
    "message": "用户信息更新成功",
    "data": {
        "id": 2,
        "username": "user01",
        "email": "updated@example.com",
        "is_active": false,
        "is_admin": false,
        "created_at": "2024-01-01T01:00:00Z",
        "last_login_at": "2024-01-01T09:30:00Z"
    }
}
```

---

### 5. 修改用户密码

**PUT** `/api/v1/users/{user_id}/password`

修改用户密码。

#### 路径参数
- `user_id` (int, 必需): 用户ID

#### 请求体
```json
{
    "old_password": "currentpassword",
    "new_password": "newsecurepassword123"
}
```

#### 请求参数说明
- `old_password` (string, 必需): 当前密码（用户修改自己密码时需要）
- `new_password` (string, 必需): 新密码，最少8字符

#### 权限说明
- 管理员：可修改任何用户密码，无需提供原密码
- 普通用户：只能修改自己密码，必须提供正确的原密码

#### 请求示例
```bash
# 用户修改自己的密码
curl -X PUT /api/v1/users/2/password \
  -H "Authorization: Bearer <user-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "old_password": "currentpassword",
    "new_password": "newsecurepassword123"
  }'

# 管理员修改用户密码（仍需提供原密码验证）
curl -X PUT /api/v1/users/2/password \
  -H "Authorization: Bearer <admin-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "old_password": "userscurrentpassword",
    "new_password": "administratorsetpassword"
  }'
```

#### 响应示例
```json
{
    "success": true,
    "code": 200,
    "message": "密码修改成功",
    "data": null
}
```

---

### 6. 管理员重置密码

**PUT** `/api/v1/users/{user_id}/admin-password`

管理员重置用户密码，无需提供原密码（仅管理员可访问）。

#### 路径参数
- `user_id` (int, 必需): 用户ID

#### 请求体
```json
{
    "new_password": "resetpassword123"
}
```

#### 请求参数说明
- `new_password` (string, 必需): 新密码，最少8字符

#### 请求示例
```bash
curl -X PUT /api/v1/users/2/admin-password \
  -H "Authorization: Bearer <admin-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "new_password": "resetpassword123"
  }'
```

#### 响应示例
```json
{
    "success": true,
    "code": 200,
    "message": "密码重置成功",
    "data": null
}
```

---

### 7. 删除用户

**DELETE** `/api/v1/users/{user_id}`

删除指定用户（仅管理员可访问，不可逆操作）。

#### 路径参数
- `user_id` (int, 必需): 用户ID

#### 安全限制
- 管理员不能删除自己
- 不能删除最后一个管理员账户
- 删除操作会同时删除用户的所有相关数据

#### 请求示例
```bash
curl -X DELETE /api/v1/users/2 \
  -H "Authorization: Bearer <admin-token>"
```

#### 响应示例
```json
{
    "success": true,
    "code": 200,
    "message": "用户删除成功",
    "data": null
}
```

---

## 错误处理

### 常见错误码

- **400 Bad Request**: 请求参数错误或业务规则违反
- **401 Unauthorized**: 未认证或token无效
- **403 Forbidden**: 权限不足
- **404 Not Found**: 用户不存在
- **409 Conflict**: 用户名或邮箱已存在

### 错误响应示例

#### 权限不足
```json
{
    "success": false,
    "code": 403,
    "message": "权限不足，只能查看自己的信息",
    "errors": null,
    "timestamp": "2024-01-01T10:30:00Z"
}
```

#### 用户名已存在
```json
{
    "success": false,
    "code": 409,
    "message": "用户名已存在",
    "errors": [
        {
            "field": "username",
            "message": "用户名 'existinguser' 已被使用"
        }
    ],
    "timestamp": "2024-01-01T10:30:00Z"
}
```

#### 原密码错误
```json
{
    "success": false,
    "code": 400,
    "message": "原密码错误",
    "errors": [
        {
            "field": "old_password",
            "message": "提供的原密码不正确"
        }
    ],
    "timestamp": "2024-01-01T10:30:00Z"
}
```

#### 业务规则违反
```json
{
    "success": false,
    "code": 400,
    "message": "不能删除最后一个管理员",
    "errors": [
        {
            "field": "user_id",
            "message": "系统必须保留至少一个管理员账户"
        }
    ],
    "timestamp": "2024-01-01T10:30:00Z"
}
```

---

## 数据模型

### 用户对象结构
```json
{
    "id": 1,
    "username": "admin",
    "email": "admin@example.com",
    "is_active": true,
    "is_admin": true,
    "created_at": "2024-01-01T00:00:00Z",
    "last_login_at": "2024-01-01T10:00:00Z"
}
```

#### 字段说明
- `id`: 用户唯一标识符
- `username`: 用户名，3-20字符，系统内唯一
- `email`: 邮箱地址，可为空，非空时必须唯一
- `is_active`: 账户激活状态，false时用户无法登录
- `is_admin`: 管理员权限标识
- `created_at`: 账户创建时间
- `last_login_at`: 最后登录时间，可为null

---

## 最佳实践

### 1. 安全建议

#### 密码策略
- 最少8个字符
- 包含大小写字母、数字和特殊字符
- 定期更换密码
- 不要在系统中硬编码密码

#### 权限管理
- 遵循最小权限原则
- 定期审查用户权限
- 及时禁用不活跃账户
- 保留操作审计日志

#### 账户安全
- 启用强密码策略
- 监控异常登录行为
- 定期清理无效账户
- 实施账户锁定机制

### 2. 操作建议

#### 用户创建
- 为新用户设置强密码
- 默认创建为非管理员账户
- 提供有效的邮箱地址
- 及时通知用户初始登录信息

#### 账户维护
- 定期检查用户活跃度
- 及时处理密码重置请求
- 监控管理员账户数量
- 保持用户信息的准确性

#### 权限管理
- 谨慎分配管理员权限
- 定期审查用户权限变更
- 建立权限变更的审批流程
- 记录所有权限相关操作

### 3. 集成建议

#### API调用
- 使用HTTPS确保数据传输安全
- 实现请求重试机制
- 处理所有可能的错误响应
- 缓存用户信息减少API调用

#### 前端集成
- 根据用户权限显示不同界面
- 实现客户端权限验证
- 提供友好的错误提示
- 支持批量操作提高效率

### 4. 监控和日志

#### 关键指标
- 活跃用户数量
- 登录失败率
- 密码重置频率
- 权限变更次数

#### 日志记录
- 用户登录/登出
- 权限变更操作
- 密码修改/重置
- 账户创建/删除

#### 异常检测
- 多次登录失败
- 异常时间段登录
- 批量账户操作
- 权限提升操作

### 5. 性能优化

#### 查询优化
- 使用索引优化用户查询
- 实现分页减少数据传输
- 缓存用户基本信息
- 批量处理减少数据库调用

#### 响应优化
- 压缩响应数据
- 使用CDN加速静态资源
- 实现客户端缓存
- 优化数据库连接池