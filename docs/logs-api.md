# 日志管理 API 文档

## 概述

日志管理API提供了完整的任务执行日志查看和管理功能，支持实时日志流式读取、历史日志查询和日志文件管理。

## 基础路径

所有日志API的基础路径为：`/api/v1/logs`

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

## 日志类型说明

### 日志来源
- **任务执行日志**: 项目执行过程中产生的标准输出
- **错误日志**: 项目执行过程中产生的错误输出
- **系统日志**: 调度器和系统组件产生的日志

### 日志级别
- **DEBUG**: 调试信息
- **INFO**: 一般信息
- **WARNING**: 警告信息
- **ERROR**: 错误信息
- **CRITICAL**: 严重错误

---

## API 接口

### 1. 获取执行日志

**GET** `/api/v1/logs/executions/{execution_id}`

获取指定执行ID的完整日志信息（包含标准输出和错误输出）。

#### 路径参数
- `execution_id` (string, 必需): 任务执行ID

#### 查询参数
- `lines` (int, 可选): 返回的日志行数，默认1000，最大10000
- `offset` (int, 可选): 跳过的行数，用于分页，默认0
- `level` (string, 可选): 日志级别筛选 (`DEBUG` | `INFO` | `WARNING` | `ERROR` | `CRITICAL`)
- `search` (string, 可选): 搜索关键词
- `format` (string, 可选): 返回格式 (`text` | `json`)，默认 `json`

#### 请求示例
```bash
curl -X GET "/api/v1/logs/executions/abc123?lines=500&search=error" \
  -H "Authorization: Bearer <token>"
```

#### 响应示例
```json
{
    "success": true,
    "code": 200,
    "message": "获取日志成功",
    "data": {
        "execution_id": "abc123",
        "task_name": "数据处理任务",
        "project_name": "Python数据分析",
        "start_time": "2024-01-01T10:00:00Z",
        "end_time": "2024-01-01T10:05:00Z",
        "status": "SUCCESS",
        "total_lines": 1250,
        "returned_lines": 500,
        "logs": [
            {
                "timestamp": "2024-01-01T10:00:01Z",
                "level": "INFO",
                "source": "stdout",
                "message": "开始处理数据文件..."
            },
            {
                "timestamp": "2024-01-01T10:00:02Z",
                "level": "INFO",
                "source": "stdout",
                "message": "读取数据完成，共1000行"
            },
            {
                "timestamp": "2024-01-01T10:04:58Z",
                "level": "ERROR",
                "source": "stderr",
                "message": "警告：发现3条无效数据"
            }
        ],
        "pagination": {
            "offset": 0,
            "limit": 500,
            "total": 1250,
            "has_more": true
        }
    }
}
```

---

### 2. 获取标准输出日志

**GET** `/api/v1/logs/executions/{execution_id}/stdout`

仅获取指定执行ID的标准输出日志。

#### 路径参数
- `execution_id` (string, 必需): 任务执行ID

#### 查询参数
- `lines` (int, 可选): 返回的日志行数，默认1000
- `tail` (boolean, 可选): 是否返回最后N行，默认false
- `format` (string, 可选): 返回格式 (`text` | `json`)，默认 `json`

#### 请求示例
```bash
curl -X GET "/api/v1/logs/executions/abc123/stdout?lines=100&tail=true&format=text" \
  -H "Authorization: Bearer <token>"
```

#### 响应示例 (format=text)
```
开始处理数据文件...
读取数据完成，共1000行
数据清洗进度: 10%
数据清洗进度: 20%
...
处理完成，输出结果到文件
任务执行成功
```

#### 响应示例 (format=json)
```json
{
    "success": true,
    "code": 200,
    "message": "获取标准输出日志成功",
    "data": {
        "execution_id": "abc123",
        "log_type": "stdout",
        "total_lines": 856,
        "returned_lines": 100,
        "logs": [
            {
                "line_number": 757,
                "timestamp": "2024-01-01T10:04:55Z",
                "message": "数据清洗进度: 95%"
            },
            {
                "line_number": 856,
                "timestamp": "2024-01-01T10:04:59Z",
                "message": "任务执行成功"
            }
        ]
    }
}
```

---

### 3. 获取错误日志

**GET** `/api/v1/logs/executions/{execution_id}/stderr`

仅获取指定执行ID的错误输出日志。

#### 路径参数
- `execution_id` (string, 必需): 任务执行ID

#### 查询参数
- `lines` (int, 可选): 返回的日志行数，默认1000
- `level` (string, 可选): 错误级别筛选 (`WARNING` | `ERROR` | `CRITICAL`)

#### 请求示例
```bash
curl -X GET "/api/v1/logs/executions/abc123/stderr?level=ERROR" \
  -H "Authorization: Bearer <token>"
```

#### 响应示例
```json
{
    "success": true,
    "code": 200,
    "message": "获取错误日志成功",
    "data": {
        "execution_id": "abc123",
        "log_type": "stderr",
        "total_lines": 12,
        "error_summary": {
            "warning_count": 8,
            "error_count": 3,
            "critical_count": 1
        },
        "logs": [
            {
                "line_number": 3,
                "timestamp": "2024-01-01T10:00:15Z",
                "level": "ERROR",
                "message": "无法解析第45行数据：格式错误"
            },
            {
                "line_number": 7,
                "timestamp": "2024-01-01T10:00:28Z",
                "level": "ERROR",
                "message": "连接数据库超时，正在重试..."
            },
            {
                "line_number": 12,
                "timestamp": "2024-01-01T10:04:30Z",
                "level": "CRITICAL",
                "message": "内存使用量超过限制"
            }
        ]
    }
}
```

---

### 4. 实时日志流 (WebSocket)

**注意**: 当前版本暂时移除了WebSocket功能，此接口暂不可用。

**WebSocket** `/api/v1/logs/executions/{execution_id}/stream`

实时接收任务执行日志流。

#### 连接示例
```javascript
const ws = new WebSocket('ws://localhost:8000/api/v1/logs/executions/abc123/stream?token=<jwt-token>');

ws.onmessage = function(event) {
    const logData = JSON.parse(event.data);
    console.log(`[${logData.timestamp}] ${logData.level}: ${logData.message}`);
};
```

#### 消息格式
```json
{
    "execution_id": "abc123",
    "timestamp": "2024-01-01T10:00:01Z",
    "level": "INFO",
    "source": "stdout",
    "message": "处理进度: 25%",
    "line_number": 123
}
```

---

### 5. 下载日志文件

**GET** `/api/v1/logs/executions/{execution_id}/download`

下载完整的日志文件。

#### 路径参数
- `execution_id` (string, 必需): 任务执行ID

#### 查询参数
- `type` (string, 可选): 日志类型 (`stdout` | `stderr` | `all`)，默认 `all`
- `format` (string, 可选): 文件格式 (`txt` | `json`)，默认 `txt`

#### 请求示例
```bash
curl -X GET "/api/v1/logs/executions/abc123/download?type=all&format=txt" \
  -H "Authorization: Bearer <token>" \
  -o "execution_abc123_logs.txt"
```

#### 响应
返回文件流，Content-Type: `application/octet-stream`

---

### 6. 获取日志统计

**GET** `/api/v1/logs/executions/{execution_id}/stats`

获取指定执行ID的日志统计信息。

#### 路径参数
- `execution_id` (string, 必需): 任务执行ID

#### 请求示例
```bash
curl -X GET /api/v1/logs/executions/abc123/stats \
  -H "Authorization: Bearer <token>"
```

#### 响应示例
```json
{
    "success": true,
    "code": 200,
    "message": "获取日志统计成功",
    "data": {
        "execution_id": "abc123",
        "task_name": "数据处理任务",
        "project_name": "Python数据分析",
        "execution_info": {
            "start_time": "2024-01-01T10:00:00Z",
            "end_time": "2024-01-01T10:05:00Z",
            "duration_seconds": 300,
            "status": "SUCCESS",
            "exit_code": 0
        },
        "log_stats": {
            "stdout_lines": 856,
            "stderr_lines": 12,
            "total_lines": 868,
            "log_size_bytes": 45678,
            "log_size_mb": 0.044
        },
        "error_stats": {
            "warning_count": 8,
            "error_count": 3,
            "critical_count": 1,
            "total_errors": 12
        },
        "performance_stats": {
            "memory_usage_mb": 128,
            "cpu_usage_percent": 15.5,
            "avg_response_time_ms": 234
        },
        "log_files": {
            "stdout_file": "/logs/tasks/abc123/output.log",
            "stderr_file": "/logs/tasks/abc123/error.log",
            "file_exists": true
        }
    }
}
```

---

### 7. 批量获取日志列表

**POST** `/api/v1/logs/batch`

批量获取多个执行ID的日志摘要信息。

#### 请求体
```json
{
    "execution_ids": ["abc123", "def456", "ghi789"],
    "include_preview": true,
    "preview_lines": 10
}
```

#### 请求示例
```bash
curl -X POST /api/v1/logs/batch \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"execution_ids": ["abc123", "def456"], "include_preview": true}'
```

#### 响应示例
```json
{
    "success": true,
    "code": 200,
    "message": "批量获取日志成功",
    "data": {
        "results": [
            {
                "execution_id": "abc123",
                "task_name": "数据处理任务",
                "status": "SUCCESS",
                "log_summary": {
                    "total_lines": 868,
                    "error_count": 12,
                    "duration_seconds": 300
                },
                "preview": [
                    "开始处理数据文件...",
                    "读取数据完成，共1000行",
                    "..."
                ]
            },
            {
                "execution_id": "def456",
                "task_name": "图片处理任务",
                "status": "FAILED",
                "log_summary": {
                    "total_lines": 234,
                    "error_count": 45,
                    "duration_seconds": 120
                },
                "preview": [
                    "开始处理图片文件...",
                    "ERROR: 内存不足",
                    "..."
                ]
            }
        ],
        "summary": {
            "total_requested": 2,
            "found": 2,
            "not_found": 0
        }
    }
}
```

---

### 8. 清理历史日志

**DELETE** `/api/v1/logs/cleanup`

清理指定时间范围外的历史日志文件。

#### 查询参数
- `before_date` (string, 必需): 清理此日期之前的日志，格式: `YYYY-MM-DD`
- `dry_run` (boolean, 可选): 是否为预演模式，默认false
- `log_type` (string, 可选): 清理的日志类型 (`stdout` | `stderr` | `all`)，默认 `all`

#### 请求示例
```bash
curl -X DELETE "/api/v1/logs/cleanup?before_date=2024-01-01&dry_run=true" \
  -H "Authorization: Bearer <token>"
```

#### 响应示例
```json
{
    "success": true,
    "code": 200,
    "message": "日志清理完成",
    "data": {
        "cleanup_info": {
            "before_date": "2024-01-01",
            "dry_run": true,
            "log_type": "all"
        },
        "results": {
            "files_to_delete": 156,
            "total_size_mb": 234.5,
            "execution_ids": ["old123", "old456", "..."],
            "oldest_log": "2023-11-15",
            "newest_log": "2023-12-31"
        },
        "note": "预演模式：未实际删除文件"
    }
}
```

---

## 日志搜索和过滤

### 1. 高级日志搜索

**GET** `/api/v1/logs/search`

在多个执行日志中搜索内容。

#### 查询参数
- `query` (string, 必需): 搜索关键词
- `project_id` (int, 可选): 限制在指定项目内搜索
- `date_from` (string, 可选): 开始日期，格式: `YYYY-MM-DD`
- `date_to` (string, 可选): 结束日期，格式: `YYYY-MM-DD`
- `level` (string, 可选): 日志级别
- `status` (string, 可选): 任务状态 (`SUCCESS` | `FAILED` | `TIMEOUT`)
- `limit` (int, 可选): 返回结果数量，默认50

#### 请求示例
```bash
curl -X GET "/api/v1/logs/search?query=database error&level=ERROR&limit=20" \
  -H "Authorization: Bearer <token>"
```

#### 响应示例
```json
{
    "success": true,
    "code": 200,
    "message": "搜索完成",
    "data": {
        "query": "database error",
        "filters": {
            "level": "ERROR",
            "limit": 20
        },
        "results": [
            {
                "execution_id": "abc123",
                "task_name": "数据同步任务",
                "project_name": "数据管道",
                "timestamp": "2024-01-01T10:00:15Z",
                "level": "ERROR",
                "message": "Database connection error: timeout after 30s",
                "context": {
                    "line_number": 45,
                    "surrounding_lines": ["connecting to database...", "Database connection error: timeout after 30s", "retrying connection..."]
                }
            }
        ],
        "summary": {
            "total_matches": 15,
            "returned": 15,
            "search_time_ms": 234
        }
    }
}
```

---

## 错误处理

### 常见错误码

- **400 Bad Request**: 请求参数错误
- **401 Unauthorized**: 未认证或token无效
- **403 Forbidden**: 权限不足
- **404 Not Found**: 执行记录或日志文件不存在
- **429 Too Many Requests**: 请求过于频繁
- **500 Internal Server Error**: 服务器内部错误

### 错误响应示例
```json
{
    "success": false,
    "code": 404,
    "message": "日志文件不存在",
    "errors": [
        {
            "field": "execution_id",
            "message": "执行记录 abc123 不存在或日志文件已被清理"
        }
    ],
    "timestamp": "2024-01-01T00:00:00Z"
}
```

---

## 使用建议

### 1. 日志查看最佳实践
- 使用分页参数避免一次性获取大量日志
- 优先使用 `tail=true` 获取最新日志
- 对于实时监控，定期轮询而不是高频请求

### 2. 日志搜索优化
- 使用具体的关键词而不是通配符
- 结合时间范围筛选提高搜索效率
- 对于复杂搜索，考虑使用多个API组合

### 3. 日志文件管理
- 定期清理历史日志文件避免磁盘空间不足
- 重要日志建议及时下载备份
- 使用 `dry_run` 模式预览清理结果

### 4. 性能考虑
- 大日志文件建议使用流式下载
- 避免在高峰期进行大量日志查询
- 使用缓存减少重复请求

### 5. 日志级别使用指南
- **DEBUG**: 详细的调试信息，生产环境通常关闭
- **INFO**: 一般的执行信息，记录关键步骤
- **WARNING**: 潜在问题，不影响执行但需要注意
- **ERROR**: 执行错误，可能影响结果
- **CRITICAL**: 严重错误，导致执行失败