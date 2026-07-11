# 任务调度 API

本文档对应当前 Web API 的任务路由。所有任务、项目和运行 ID 均使用公开字符串 ID。

## 基本信息

- Base URL：`/api/v1/tasks`
- 认证：`Authorization: Bearer <access_token>`
- 请求格式：`application/json`
- 枚举值均为小写

任务状态：

- `pending`
- `dispatching`
- `queued`
- `running`
- `success`
- `failed`
- `cancelled`
- `timeout`
- `paused`
- `rejected`
- `skipped`

调度类型：

- `once`
- `cron`
- `interval`
- `date`

执行策略：

- `fixed`
- `specified`
- `auto`
- `prefer`

## 创建任务

`POST /api/v1/tasks`

公共字段：

| 字段 | 类型 | 必需 | 说明 |
|---|---|---:|---|
| `name` | string | 是 | 3-255 字符 |
| `description` | string | 否 | 最多 500 字符 |
| `project_id` | string | 是 | 项目公开 ID |
| `schedule_type` | string | 是 | `once/cron/interval/date` |
| `is_active` | boolean | 否 | 默认 `true` |
| `max_instances` | integer | 否 | 1-10，默认 1 |
| `timeout_seconds` | integer | 否 | 大于 0，默认 3600 |
| `retry_count` | integer | 否 | 0-10，默认 3 |
| `retry_delay` | integer | 否 | 大于 0，默认 60 |
| `execution_params` | object | 否 | JSON 序列化后不超过 64 KiB |
| `environment_vars` | object | 否 | JSON 序列化后不超过 64 KiB |
| `execution_strategy` | string | 否 | 执行策略 |
| `specified_worker_id` | string | 否 | 指定 Worker 公开 ID |

不同调度类型的必需字段：

| `schedule_type` | 必需字段 |
|---|---|
| `cron` | `cron_expression`，标准五段 crontab，最多 100 字符 |
| `interval` | `interval_seconds`，大于 0 |
| `date` | `scheduled_time`，ISO 8601 时间 |
| `once` | 无额外字段 |

CRON 示例：

```bash
curl -X POST /api/v1/tasks \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "数据同步任务",
    "project_id": "0123456789abcdef0123456789abcdef",
    "schedule_type": "cron",
    "cron_expression": "0 */2 * * *",
    "is_active": true
  }'
```

INTERVAL 示例：

```json
{
  "name": "增量抓取任务",
  "project_id": "0123456789abcdef0123456789abcdef",
  "schedule_type": "interval",
  "interval_seconds": 300
}
```

## 查询任务

### 列表

`GET /api/v1/tasks`

查询参数：

- `page`：页码，默认 1
- `size`：每页数量，1-100
- `status`：小写任务状态
- `is_active`：是否激活
- `project_id`：项目公开 ID
- `schedule_type`：小写调度类型
- `specified_worker_id`：指定 Worker 公开 ID
- `worker_id`：按实际执行 Worker 筛选

```bash
curl "/api/v1/tasks?page=1&size=20&status=running" \
  -H "Authorization: Bearer <token>"
```

### 详情

`GET /api/v1/tasks/{task_id}`

### 运行中任务

`GET /api/v1/tasks/running`

### 全局任务统计

`GET /api/v1/tasks/stats`

## 更新与删除

### 更新任务

`PUT /api/v1/tasks/{task_id}`

支持更新名称、描述、激活状态、触发器字段、重试参数、执行参数和 Worker 策略。提交 `cron_expression` 时会立即校验 crontab 语法。

```bash
curl -X PUT /api/v1/tasks/{task_id} \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"cron_expression":"0 */4 * * *"}'
```

### 删除任务

`DELETE /api/v1/tasks/{task_id}`

### 批量删除

`POST /api/v1/tasks/batch-delete`

### 批量操作

`POST /api/v1/tasks/batch`

请求体包含 `task_ids`、`action` 和可选 `execution_config`。`action` 支持 `start/stop/cancel/delete/enable/disable`。

## 调度控制

- 暂停：`POST /api/v1/tasks/{task_id}/pause`
- 恢复：`POST /api/v1/tasks/{task_id}/resume`
- 立即触发：`POST /api/v1/tasks/{task_id}/trigger`
- 立即执行：`POST /api/v1/tasks/{task_id}/execute`
- 启用或停用：`PATCH /api/v1/tasks/{task_id}/toggle`
- 复制：`POST /api/v1/tasks/{task_id}/duplicate`
- 校验 CRON：`POST /api/v1/tasks/validate-cron`

`validate-cron` 请求：

```json
{"expression":"0 2 * * *"}
```

## 依赖与导入导出

- 查询依赖：`GET /api/v1/tasks/{task_id}/dependencies`
- 更新依赖：`PUT /api/v1/tasks/{task_id}/dependencies`
- 导出任务：`GET /api/v1/tasks/{task_id}/export`
- 导入任务：`POST /api/v1/tasks/import`
- 模板列表：`GET /api/v1/tasks/templates`
- 从模板创建：`POST /api/v1/tasks/templates/{template_id}/create`

## 运行记录

- 任务运行列表：`GET /api/v1/tasks/{task_id}/runs`
- 调度历史：`GET /api/v1/tasks/{task_id}/schedule-history`
- 任务统计：`GET /api/v1/tasks/{task_id}/stats`
- 停止运行：`POST /api/v1/tasks/runs/{run_id}/stop`
- 运行日志：`GET /api/v1/tasks/runs/{run_id}/logs`
- 下载日志：`GET /api/v1/tasks/runs/{run_id}/logs/download`

日志查询支持 `page`、`size` 和 `log_type`。不存在 `/api/v1/scheduler/executions/{id}` 端点。

## 响应与错误

普通响应使用统一 envelope：

```json
{
  "success": true,
  "code": 200,
  "message": "操作成功",
  "data": {}
}
```

列表响应额外包含 `pagination`。常见状态码：

- `400`：业务参数或调度配置错误
- `401`：认证失败或会话已撤销
- `403`：无资源权限
- `404`：任务、项目或运行不存在
- `413`：请求体超过应用限制
- `422`：请求 schema、枚举或 CRON 校验失败
