# 项目管理 API 文档

## 概述

项目管理API提供了完整的项目生命周期管理功能，支持三种项目类型：
- **文件项目 (FILE)**: 上传并执行Python文件或压缩包
- **规则项目 (RULE)**: 配置网页抓取规则，支持多种引擎
- **代码项目 (CODE)**: 直接编写和执行代码片段

## 基础路径

所有项目API的基础路径为：`/api/v1/projects`

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

## 项目类型枚举

```python
ProjectType:
- FILE: 文件项目
- RULE: 规则项目  
- CODE: 代码项目

ProjectStatus:
- DRAFT: 草稿
- ACTIVE: 激活
- INACTIVE: 停用
- ARCHIVED: 归档
```

---

## API 接口

### 1. 创建项目

**POST** `/api/v1/projects`

创建新项目，支持文件、规则、代码三种类型。

#### 请求参数 (Form Data)

**通用参数：**
- `name` (string, 必需): 项目名称 (3-50字符)
- `description` (string, 可选): 项目描述 (最多500字符)
- `type` (string, 必需): 项目类型 (`FILE` | `RULE` | `CODE`)
- `tags` (string, 可选): 项目标签，逗号分隔
- `dependencies` (string, 可选): Python依赖包JSON数组

**文件项目参数：**
- `entry_point` (string, 可选): 入口文件路径
- `runtime_config` (string, 可选): 运行时配置JSON
- `environment_vars` (string, 可选): 环境变量JSON
- `file` (file, 必需): 项目文件
- `files` (file[], 可选): 多个项目文件

**规则项目参数：**
- `engine` (string): 采集引擎 (`browser` | `requests` | `curl_cffi`)
- `target_url` (string, 必需): 目标URL
- `url_pattern` (string, 可选): URL匹配模式
- `request_method` (string): 请求方法，默认 `GET`
- `callback_type` (string): 回调类型 (`list` | `detail`)
- `extraction_rules` (string, 必需): 提取规则JSON
- `pagination_config` (string, 可选): 分页配置JSON
- `max_pages` (int): 最大页数，默认10
- `start_page` (int): 起始页码，默认1
- `request_delay` (float): 请求延迟秒数
- `priority` (int): 优先级，默认0
- `headers` (string, 可选): 请求头JSON
- `cookies` (string, 可选): Cookies JSON

**代码项目参数：**
- `language` (string): 编程语言，默认 `python`
- `version` (string, 可选): 语言版本
- `code_entry_point` (string, 可选): 代码入口点
- `documentation` (string, 可选): 文档说明
- `code_content` (string): 代码内容
- `code_file` (file, 可选): 代码文件

#### 请求示例

**创建文件项目：**
```bash
curl -X POST /api/v1/projects \
  -H "Authorization: Bearer <token>" \
  -F "name=Python测试项目" \
  -F "description=一个Python测试项目" \
  -F "type=FILE" \
  -F "entry_point=main.py" \
  -F "file=@test_project.zip"
```

**创建规则项目：**
```bash
curl -X POST /api/v1/projects \
  -H "Authorization: Bearer <token>" \
  -F "name=新闻抓取" \
  -F "type=RULE" \
  -F "engine=requests" \
  -F "target_url=https://example.com/news" \
  -F 'extraction_rules=[{"name":"title","selector":"h1","attr":"text"}]'
```

#### 响应示例
```json
{
    "success": true,
    "code": 201,
    "message": "项目创建成功",
    "data": {
        "id": 1,
        "name": "Python测试项目",
        "description": "一个Python测试项目",
        "type": "FILE",
        "status": "DRAFT",
        "tags": [],
        "created_at": "2024-01-01T00:00:00Z",
        "file_info": {
            "original_name": "test_project.zip",
            "file_size": 1024,
            "file_hash": "abc123..."
        }
    }
}
```

---

### 2. 获取项目列表

**GET** `/api/v1/projects`

获取当前用户的项目列表，支持分页和筛选。

#### 查询参数

- `page` (int, 可选): 页码，默认1
- `size` (int, 可选): 每页数量，默认20，最大100
- `type` (string, 可选): 项目类型筛选
- `status` (string, 可选): 项目状态筛选
- `tag` (string, 可选): 标签筛选

#### 请求示例
```bash
curl -X GET "/api/v1/projects?page=1&size=10&type=FILE" \
  -H "Authorization: Bearer <token>"
```

#### 响应示例
```json
{
    "success": true,
    "code": 200,
    "message": "获取成功",
    "data": {
        "items": [
            {
                "id": 1,
                "name": "Python测试项目",
                "description": "一个Python测试项目",
                "type": "FILE",
                "status": "ACTIVE",
                "tags": ["python", "test"],
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T01:00:00Z"
            }
        ],
        "pagination": {
            "page": 1,
            "size": 10,
            "total": 1,
            "pages": 1
        }
    }
}
```

---

### 3. 获取项目详情

**GET** `/api/v1/projects/{project_id}`

获取指定项目的详细信息。

#### 路径参数
- `project_id` (int, 必需): 项目ID

#### 请求示例
```bash
curl -X GET /api/v1/projects/1 \
  -H "Authorization: Bearer <token>"
```

#### 响应示例
```json
{
    "success": true,
    "code": 200,
    "message": "获取成功",
    "data": {
        "id": 1,
        "name": "Python测试项目",
        "description": "一个Python测试项目",
        "type": "FILE",
        "status": "ACTIVE",
        "tags": ["python", "test"],
        "dependencies": ["requests", "beautifulsoup4"],
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T01:00:00Z",
        "file_detail": {
            "entry_point": "main.py",
            "runtime_config": {},
            "environment_vars": {},
            "file_path": "projects/1/main.py",
            "original_name": "main.py",
            "file_size": 1024,
            "file_hash": "abc123...",
            "is_compressed": false
        }
    }
}
```

---

### 4. 更新项目

**PUT** `/api/v1/projects/{project_id}`

更新项目基本信息。

#### 路径参数
- `project_id` (int, 必需): 项目ID

#### 请求体
```json
{
    "name": "更新的项目名称",
    "description": "更新的描述",
    "status": "ACTIVE",
    "tags": ["updated", "tag"]
}
```

#### 请求示例
```bash
curl -X PUT /api/v1/projects/1 \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"name": "更新的项目名称", "status": "ACTIVE"}'
```

#### 响应示例
```json
{
    "success": true,
    "code": 200,
    "message": "项目更新成功",
    "data": {
        "id": 1,
        "name": "更新的项目名称",
        "status": "ACTIVE",
        "updated_at": "2024-01-01T02:00:00Z"
    }
}
```

---

### 5. 删除项目

**DELETE** `/api/v1/projects/{project_id}`

删除指定项目（不可逆操作）。

#### 路径参数
- `project_id` (int, 必需): 项目ID

#### 请求示例
```bash
curl -X DELETE /api/v1/projects/1 \
  -H "Authorization: Bearer <token>"
```

#### 响应示例
```json
{
    "success": true,
    "code": 200,
    "message": "项目删除成功",
    "data": null
}
```

---

### 6. 批量删除项目

**POST** `/api/v1/projects/batch-delete`

批量删除多个项目（不可逆操作）。

#### 请求体
```json
{
    "project_ids": [1, 2, 3]
}
```

#### 请求示例
```bash
curl -X POST /api/v1/projects/batch-delete \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"project_ids": [1, 2, 3]}'
```

#### 响应示例
```json
{
    "success": true,
    "task_id": "batch-delete-uuid-123",
    "message": "批量删除任务已提交到后台执行",
    "background": true
}
```

---

### 7. 统一项目更新

**PUT** `/api/v1/projects/{project_id}/unified`

统一的项目配置更新接口，支持所有项目类型。

#### 路径参数
- `project_id` (int, 必需): 项目ID

#### 请求体示例

**更新文件项目配置：**
```json
{
    "file_config": {
        "entry_point": "app.py",
        "runtime_config": {"workers": 2},
        "environment_vars": {"ENV": "production"}
    }
}
```

**更新规则项目配置：**
```json
{
    "rule_config": {
        "target_url": "https://new-target.com",
        "extraction_rules": [
            {
                "name": "title",
                "selector": "h1",
                "attr": "text"
            }
        ],
        "headers": {
            "User-Agent": "AntCode/1.0"
        }
    }
}
```

**更新代码项目配置：**
```json
{
    "code_config": {
        "code_content": "print('Hello Updated World!')",
        "language": "python",
        "version": "3.11"
    }
}
```

---

### 8. 生成任务JSON

**POST** `/api/v1/projects/{project_id}/generate-task`

为规则项目生成可执行的任务JSON配置。

#### 路径参数
- `project_id` (int, 必需): 项目ID

#### 请求示例
```bash
curl -X POST /api/v1/projects/1/generate-task \
  -H "Authorization: Bearer <token>"
```

#### 响应示例
```json
{
    "success": true,
    "code": 200,
    "message": "任务JSON生成成功",
    "data": {
        "url": "https://example.com/data",
        "callback": "list",
        "method": "GET",
        "meta": {
            "fetch_type": "requests",
            "task_id": "crawler-20240101120000123-abc7def",
            "worker_id": "Scraper-Node-Default",
            "rules": [
                {
                    "name": "title",
                    "selector": "h1",
                    "attr": "text"
                }
            ]
        },
        "headers": {},
        "cookies": {},
        "priority": 0,
        "dont_filter": false
    }
}
```

---

## 错误处理

### 常见错误码

- **400 Bad Request**: 请求参数错误
- **401 Unauthorized**: 未认证或token无效
- **403 Forbidden**: 权限不足
- **404 Not Found**: 项目不存在
- **409 Conflict**: 项目名称重复
- **413 Request Entity Too Large**: 文件过大
- **415 Unsupported Media Type**: 文件类型不支持
- **422 Unprocessable Entity**: 请求参数验证失败

### 错误响应示例
```json
{
    "success": false,
    "code": 400,
    "message": "请求参数验证失败",
    "errors": [
        {
            "field": "name",
            "message": "项目名称长度必须在3-50字符之间"
        }
    ],
    "timestamp": "2024-01-01T00:00:00Z"
}
```

---

## 最佳实践

### 1. 文件上传限制
- 单个文件最大100MB
- 支持的文件类型：`.py`, `.zip`, `.tar.gz`, `.txt`, `.json`, `.md`, `.yml`, `.yaml`
- 压缩包会自动解压到项目目录

### 2. 项目命名规范
- 名称长度：3-50字符
- 同一用户下项目名称唯一
- 建议使用有意义的描述性名称

### 3. 标签使用
- 支持多个标签，用于项目分类
- 标签可用于列表筛选
- 建议使用一致的标签命名

### 4. 配置JSON格式
提取规则示例：
```json
[
    {
        "name": "title",
        "selector": "h1.title",
        "attr": "text"
    },
    {
        "name": "link",
        "selector": "a.link",
        "attr": "href"
    }
]
```

分页配置示例：
```json
{
    "method": "url_pattern",
    "start_page": 1,
    "max_pages": 10,
    "page_param": "page"
}
```