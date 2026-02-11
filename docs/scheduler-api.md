# 任务调度 API 文档

## 概述

任务调度API提供了完整的任务调度管理功能，支持创建、更新、执行和监控定时任务。系统支持多种调度模式：一次性任务、定时任务、间隔任务等，并提供详细的执行历史和统计信息。

## 基础路径

所有任务调度API的基础路径为：`/api/v1/scheduler`

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

## 任务状态枚举

```python
TaskStatus:
- PENDING: 等待执行
- RUNNING: 执行中
- SUCCESS: 成功完成
- FAILED: 执行失败
- TIMEOUT: 执行超时
- CANCELLED: 已取消

ScheduleType:
- ONCE: 一次性任务
- CRON: CRON表达式
- INTERVAL: 间隔执行
- DATE: 指定日期
```

---

## API 接口

### 1. 创建调度任务

**POST** `/api/v1/scheduler/tasks`

创建新的调度任务，支持多种调度类型和执行配置。

#### 请求体
```json
{
    "name": "数据备份任务",
    "description": "每日数据备份",
    "project_id": 1,
    "schedule_type": "CRON",
    "schedule_config": "0 2 * * *",
    "is_active": true,
    "timeout_seconds": 3600,
    "max_instances": 1,
    "retry_count": 3,
    "priority": 0
}
```

#### 请求参数说明
- `name` (string, 必需): 任务名称 (3-50字符)
- `description` (string, 可选): 任务描述
- `project_id` (int, 必需): 关联项目ID
- `schedule_type` (string, 必需): 调度类型 (`ONCE` | `CRON` | `INTERVAL` | `DATE`)
- `schedule_config` (string, 必需): 调度配置 (CRON表达式、间隔秒数或日期)
- `is_active` (boolean): 是否激活，默认true
- `timeout_seconds` (int): 超时时间，默认3600秒
- `max_instances` (int): 最大实例数，默认1
- `retry_count` (int): 重试次数，默认0
- `priority` (int): 优先级，默认0

#### 请求示例
```bash
curl -X POST /api/v1/scheduler/tasks \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "数据同步任务",
    "project_id": 1,
    "schedule_type": "CRON",
    "schedule_config": "0 */2 * * *",
    "is_active": true
  }'
```

#### 响应示例
```json
{
    "success": true,
    "code": 200,
    "message": "任务创建成功",
    "data": {
        "id": 1,
        "name": "数据同步任务",
        "description": null,
        "project_id": 1,
        "schedule_type": "CRON",
        "schedule_config": "0 */2 * * *",
        "is_active": true,
        "status": "PENDING",
        "timeout_seconds": 3600,
        "max_instances": 1,
        "retry_count": 0,
        "priority": 0,
        "next_run_time": "2024-01-01T02:00:00Z",
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:00:00Z"
    }
}
```

---

### 2. 获取任务列表

**GET** `/api/v1/scheduler/tasks`

获取当前用户的调度任务列表，支持分页和筛选。

#### 查询参数
- `page` (int, 可选): 页码，默认1
- `size` (int, 可选): 每页数量，默认20，最大100
- `status` (string, 可选): 任务状态筛选
- `is_active` (boolean, 可选): 激活状态筛选

#### 请求示例
```bash
curl -X GET "/api/v1/scheduler/tasks?page=1&size=10&is_active=true" \
  -H "Authorization: Bearer <token>"
```

#### 响应示例
```json
{
    "total": 25,
    "page": 1,
    "size": 10,
    "items": [
        {
            "id": 1,
            "name": "数据同步任务",
            "description": "每2小时同步一次数据",
            "project_id": 1,
            "schedule_type": "CRON",
            "schedule_config": "0 */2 * * *",
            "is_active": true,
            "status": "PENDING",
            "next_run_time": "2024-01-01T02:00:00Z",
            "last_execution": {
                "execution_id": "exec-123",
                "status": "SUCCESS",
                "start_time": "2024-01-01T00:00:00Z",
                "duration_seconds": 45
            },
            "created_at": "2024-01-01T00:00:00Z"
        }
    ]
}
```

---

### 3. 获取任务详情

**GET** `/api/v1/scheduler/tasks/{task_id}`

获取指定任务的详细信息。

#### 路径参数
- `task_id` (int, 必需): 任务ID

#### 请求示例
```bash
curl -X GET /api/v1/scheduler/tasks/1 \
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
        "name": "数据同步任务",
        "description": "每2小时同步一次数据",
        "project_id": 1,
        "project_name": "数据处理项目",
        "schedule_type": "CRON",
        "schedule_config": "0 */2 * * *",
        "is_active": true,
        "status": "PENDING",
        "timeout_seconds": 3600,
        "max_instances": 1,
        "retry_count": 0,
        "priority": 0,
        "next_run_time": "2024-01-01T02:00:00Z",
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:00:00Z",
        "execution_summary": {
            "total_executions": 15,
            "success_count": 13,
            "failed_count": 2,
            "last_success": "2024-01-01T00:00:00Z",
            "last_failure": "2023-12-31T22:00:00Z"
        }
    }
}
```

---

### 4. 更新任务

**PUT** `/api/v1/scheduler/tasks/{task_id}`

更新任务配置信息。

#### 路径参数
- `task_id` (int, 必需): 任务ID

#### 请求体
```json
{
    "name": "更新后的任务名称",
    "description": "更新后的描述",
    "schedule_config": "0 */4 * * *",
    "is_active": false,
    "timeout_seconds": 7200
}
```

#### 请求示例
```bash
curl -X PUT /api/v1/scheduler/tasks/1 \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "更新的数据同步任务",
    "schedule_config": "0 */4 * * *"
  }'
```

#### 响应示例
```json
{
    "success": true,
    "code": 200,
    "message": "任务更新成功",
    "data": {
        "id": 1,
        "name": "更新的数据同步任务",
        "schedule_config": "0 */4 * * *",
        "updated_at": "2024-01-01T01:00:00Z"
    }
}
```

---

### 5. 删除任务

**DELETE** `/api/v1/scheduler/tasks/{task_id}`

删除指定任务（不可逆操作）。

#### 路径参数
- `task_id` (int, 必需): 任务ID

#### 请求示例
```bash
curl -X DELETE /api/v1/scheduler/tasks/1 \
  -H "Authorization: Bearer <token>"
```

#### 响应示例
```json
{
    "success": true,
    "code": 200,
    "message": "任务删除成功",
    "data": null
}
```

---

### 6. 暂停任务

**POST** `/api/v1/scheduler/tasks/{task_id}/pause`

暂停指定任务的调度执行。

#### 路径参数
- `task_id` (int, 必需): 任务ID

#### 请求示例
```bash
curl -X POST /api/v1/scheduler/tasks/1/pause \
  -H "Authorization: Bearer <token>"
```

#### 响应示例
```json
{
    "success": true,
    "code": 200,
    "message": "任务已暂停",
    "data": null
}
```

---

### 7. 恢复任务

**POST** `/api/v1/scheduler/tasks/{task_id}/resume`

恢复暂停的任务调度。

#### 路径参数
- `task_id` (int, 必需): 任务ID

#### 请求示例
```bash
curl -X POST /api/v1/scheduler/tasks/1/resume \
  -H "Authorization: Bearer <token>"
```

#### 响应示例
```json
{
    "success": true,
    "code": 200,
    "message": "任务已恢复",
    "data": null
}
```

---

### 8. 立即触发任务

**POST** `/api/v1/scheduler/tasks/{task_id}/trigger`

立即执行指定任务，不影响正常调度。

#### 路径参数
- `task_id` (int, 必需): 任务ID

#### 请求示例
```bash
curl -X POST /api/v1/scheduler/tasks/1/trigger \
  -H "Authorization: Bearer <token>"
```

#### 响应示例
```json
{
    "success": true,
    "code": 200,
    "message": "任务已触发",
    "data": null
}
```

---

### 9. 获取任务执行历史

**GET** `/api/v1/scheduler/tasks/{task_id}/executions`

获取指定任务的执行历史记录。

#### 路径参数
- `task_id` (int, 必需): 任务ID

#### 查询参数
- `page` (int, 可选): 页码，默认1
- `size` (int, 可选): 每页数量，默认20，最大100
- `status` (string, 可选): 执行状态筛选
- `start_date` (datetime, 可选): 开始时间筛选
- `end_date` (datetime, 可选): 结束时间筛选

#### 请求示例
```bash
curl -X GET "/api/v1/scheduler/tasks/1/executions?page=1&size=10&status=SUCCESS" \
  -H "Authorization: Bearer <token>"
```

#### 响应示例
```json
{
    "total": 45,
    "page": 1,
    "size": 10,
    "items": [
        {
            "execution_id": "exec-20240101-001",
            "task_id": 1,
            "status": "SUCCESS",
            "start_time": "2024-01-01T02:00:00Z",
            "end_time": "2024-01-01T02:00:45Z",
            "duration_seconds": 45,
            "exit_code": 0,
            "output_summary": "处理了1000条记录，成功同步到数据库",
            "error_message": null,
            "log_file_path": "/logs/tasks/exec-20240101-001/output.log",
            "error_log_path": "/logs/tasks/exec-20240101-001/error.log",
            "created_at": "2024-01-01T02:00:00Z"
        }
    ]
}
```

---

### 10. 获取执行详情

**GET** `/api/v1/scheduler/executions/{execution_id}`

获取指定执行记录的详细信息。

#### 路径参数
- `execution_id` (string, 必需): 执行记录ID

#### 请求示例
```bash
curl -X GET /api/v1/scheduler/executions/exec-20240101-001 \
  -H "Authorization: Bearer <token>"
```

#### 响应示例
```json
{
    "success": true,
    "code": 200,
    "message": "获取成功",
    "data": {
        "execution_id": "exec-20240101-001",
        "task_id": 1,
        "task_name": "数据同步任务",
        "status": "SUCCESS",
        "start_time": "2024-01-01T02:00:00Z",
        "end_time": "2024-01-01T02:00:45Z",
        "duration_seconds": 45,
        "exit_code": 0,
        "output_summary": "处理了1000条记录，成功同步到数据库",
        "error_message": null,
        "log_file_path": "/logs/tasks/exec-20240101-001/output.log",
        "error_log_path": "/logs/tasks/exec-20240101-001/error.log",
        "workspace_path": "/tmp/workspaces/exec-20240101-001",
        "resource_usage": {
            "max_memory_mb": 128,
            "avg_cpu_percent": 15.5
        },
        "created_at": "2024-01-01T02:00:00Z"
    }
}
```

---

### 11. 获取执行日志

**GET** `/api/v1/scheduler/executions/{execution_id}/logs/file`

获取执行记录的日志文件内容。

#### 路径参数
- `execution_id` (string, 必需): 执行记录ID

#### 查询参数
- `log_type` (string): 日志类型 (`output` | `error`)，默认 `output`
- `lines` (int, 可选): 返回的日志行数，最大10000

#### 请求示例
```bash
curl -X GET "/api/v1/scheduler/executions/exec-20240101-001/logs/file?log_type=output&lines=100" \
  -H "Authorization: Bearer <token>"
```

#### 响应示例
```json
{
    "success": true,
    "code": 200,
    "message": "获取成功",
    "data": {
        "execution_id": "exec-20240101-001",
        "log_type": "output",
        "content": [
            "2024-01-01 02:00:01 [INFO] 开始执行数据同步任务",
            "2024-01-01 02:00:05 [INFO] 连接到数据库成功",
            "2024-01-01 02:00:10 [INFO] 开始读取源数据",
            "2024-01-01 02:00:30 [INFO] 数据处理进度: 50%",
            "2024-01-01 02:00:45 [INFO] 任务执行完成，处理了1000条记录"
        ],
        "file_path": "/logs/tasks/exec-20240101-001/output.log",
        "file_size": 2048,
        "lines_count": 25,
        "last_modified": "2024-01-01T02:00:45Z"
    }
}
```

---

### 12. 获取任务统计

**GET** `/api/v1/scheduler/tasks/{task_id}/stats`

获取指定任务的统计信息。

#### 路径参数
- `task_id` (int, 必需): 任务ID

#### 请求示例
```bash
curl -X GET /api/v1/scheduler/tasks/1/stats \
  -H "Authorization: Bearer <token>"
```

#### 响应示例
```json
{
    "success": true,
    "code": 200,
    "message": "获取成功",
    "data": {
        "total_executions": 50,
        "success_count": 47,
        "failed_count": 3,
        "success_rate": 0.94,
        "average_duration": 42.5,
        "last_execution": {
            "execution_id": "exec-20240101-001",
            "status": "SUCCESS",
            "start_time": "2024-01-01T02:00:00Z",
            "duration_seconds": 45
        },
        "execution_trends": {
            "daily_counts": [
                {"date": "2024-01-01", "success": 12, "failed": 0},
                {"date": "2023-12-31", "success": 11, "failed": 1}
            ]
        }
    }
}
```

---

### 13. 获取系统指标

**GET** `/api/v1/scheduler/metrics`

获取调度系统的运行指标和健康状态。

#### 请求示例
```bash
curl -X GET /api/v1/scheduler/metrics \
  -H "Authorization: Bearer <token>"
```

#### 响应示例
```json
{
    "success": true,
    "code": 200,
    "message": "获取成功",
    "data": {
        "cpu_percent": 25.3,
        "memory_percent": 45.7,
        "disk_usage": 67.2,
        "active_tasks": 3,
        "scheduler_status": "RUNNING",
        "queue_status": {
            "pending_tasks": 5,
            "running_tasks": 3,
            "completed_today": 42
        },
        "database_status": "HEALTHY",
        "redis_status": "HEALTHY",
        "last_updated": "2024-01-01T10:30:00Z"
    }
}
```

---

### 14. 获取运行中的任务

**GET** `/api/v1/scheduler/running`

获取当前正在运行的任务列表。

#### 请求示例
```bash
curl -X GET /api/v1/scheduler/running \
  -H "Authorization: Bearer <token>"
```

#### 响应示例
```json
{
    "success": true,
    "code": 200,
    "message": "获取成功",
    "data": [
        {
            "task_id": 1,
            "task_name": "数据同步任务",
            "execution_id": "exec-20240101-003",
            "start_time": "2024-01-01T10:00:00Z",
            "running_time": 180,
            "status": "RUNNING",
            "progress": {
                "current_step": "数据处理",
                "progress_percent": 65
            }
        }
    ]
}
```

---

### 15. 清理执行工作目录

**POST** `/api/v1/scheduler/cleanup-workspaces`

手动清理历史执行的工作目录（仅管理员）。

#### 查询参数
- `max_age_hours` (int): 最大保留时间（小时），默认24

#### 请求示例
```bash
curl -X POST "/api/v1/scheduler/cleanup-workspaces?max_age_hours=48" \
  -H "Authorization: Bearer <token>"
```

#### 响应示例
```json
{
    "success": true,
    "code": 200,
    "message": "清理完成，已删除超过 48 小时的工作目录",
    "data": {
        "cleanup_summary": {
            "directories_removed": 25,
            "space_freed_mb": 512.3,
            "oldest_removed": "2023-12-30T10:00:00Z",
            "cleanup_time": "2024-01-01T10:35:00Z"
        }
    }
}
```

---

## 调度配置说明

### CRON表达式格式
```
* * * * *
| | | | |
| | | | +-- 星期几 (0-7, 0和7都表示星期日)
| | | +---- 月份 (1-12)
| | +------ 日期 (1-31)
| +-------- 小时 (0-23)
+---------- 分钟 (0-59)
```

### 常用CRON示例
- `0 0 * * *`: 每天午夜执行
- `0 */2 * * *`: 每2小时执行
- `30 9 * * 1-5`: 工作日上午9:30执行
- `0 0 1 * *`: 每月1号执行
- `0 0 * * 0`: 每周日执行

### 间隔调度格式
- 以秒为单位的数字，如 `3600` 表示每小时执行
- 支持的最小间隔为60秒

### 日期调度格式
- ISO 8601格式：`2024-01-01T15:30:00Z`
- 支持时区设置

---

## 错误处理

### 常见错误码

- **400 Bad Request**: 请求参数错误或调度配置无效
- **401 Unauthorized**: 未认证或token无效
- **403 Forbidden**: 权限不足（如非管理员操作）
- **404 Not Found**: 任务或执行记录不存在
- **409 Conflict**: 任务名称重复或状态冲突
- **500 Internal Server Error**: 服务器内部错误

### 错误响应示例
```json
{
    "success": false,
    "code": 400,
    "message": "调度配置无效",
    "errors": [
        {
            "field": "schedule_config",
            "message": "CRON表达式格式错误：'0 25 * * *' 小时部分超出范围"
        }
    ],
    "timestamp": "2024-01-01T10:30:00Z"
}
```

---

## 最佳实践

### 1. 任务调度设计原则
- 合理设置超时时间避免任务长时间挂起
- 使用合适的重试次数和间隔
- 避免在高峰期调度大量任务
- 为重要任务设置适当的优先级

### 2. 监控和维护
- 定期查看任务执行统计和失败日志
- 监控系统资源使用情况
- 及时清理历史日志和工作目录
- 对失败任务进行及时处理

### 3. 性能优化
- 限制并发执行的任务数量
- 合理配置任务超时时间
- 使用批处理减少数据库操作
- 定期清理历史执行记录

### 4. 安全考虑
- 只有任务所有者可以查看和操作任务
- 管理员操作需要额外权限验证
- 日志中避免记录敏感信息
- 定期轮换JWT密钥

### 5. 日志管理
- 任务输出和错误分别记录
- 设置合理的日志保留策略
- 对大量输出进行截断保护
- 提供实时日志流查看功能

### 6. 调度配置建议
- 使用标准CRON表达式确保可读性
- 避免在整点同时执行大量任务
- 为周期性任务设置合理的执行间隔
- 考虑系统时区和夏令时影响