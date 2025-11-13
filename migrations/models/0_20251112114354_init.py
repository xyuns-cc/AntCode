from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS `interpreters` (
    `id` BIGINT NOT NULL PRIMARY KEY AUTO_INCREMENT,
    `tool` VARCHAR(20) NOT NULL COMMENT '工具/语言，如 python' DEFAULT 'python',
    `version` VARCHAR(20) NOT NULL COMMENT '版本号',
    `install_dir` VARCHAR(500) NOT NULL COMMENT '安装目录',
    `python_bin` VARCHAR(500) NOT NULL COMMENT '解释器可执行路径',
    `status` VARCHAR(20) NOT NULL COMMENT '状态' DEFAULT 'installed',
    `source` VARCHAR(5) NOT NULL COMMENT '来源：mise/local' DEFAULT 'mise',
    `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    `created_by` BIGINT COMMENT '创建者ID',
    UNIQUE KEY `uid_interpreter_tool_be5dc2` (`tool`, `version`, `source`)
) CHARACTER SET utf8mb4 COMMENT='语言解释器（当前仅支持 Python）';
CREATE TABLE IF NOT EXISTS `projects` (
    `id` BIGINT NOT NULL PRIMARY KEY AUTO_INCREMENT,
    `name` VARCHAR(255) NOT NULL UNIQUE COMMENT '项目名称',
    `description` LONGTEXT COMMENT '项目描述',
    `type` VARCHAR(4) NOT NULL COMMENT '项目类型',
    `status` VARCHAR(8) NOT NULL COMMENT '项目状态' DEFAULT 'draft',
    `tags` JSON NOT NULL COMMENT '项目标签',
    `dependencies` JSON COMMENT 'Python依赖包',
    `python_version` VARCHAR(20) COMMENT '绑定的Python版本',
    `venv_scope` VARCHAR(7) COMMENT '虚拟环境作用域：shared/private',
    `venv_path` VARCHAR(500) COMMENT '虚拟环境路径',
    `current_venv_id` BIGINT COMMENT '当前绑定的虚拟环境ID（应用层外键）',
    `created_at` DATETIME(6) NOT NULL COMMENT '创建时间' DEFAULT CURRENT_TIMESTAMP(6),
    `updated_at` DATETIME(6) NOT NULL COMMENT '更新时间' DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    `updated_by` BIGINT COMMENT '更新者ID',
    `user_id` BIGINT NOT NULL COMMENT '创建者ID',
    `download_count` INT NOT NULL COMMENT '下载次数' DEFAULT 0,
    `star_count` INT NOT NULL COMMENT '收藏次数' DEFAULT 0,
    KEY `idx_projects_name_7b5b92` (`name`),
    KEY `idx_projects_type_46042a` (`type`),
    KEY `idx_projects_status_ad9f12` (`status`),
    KEY `idx_projects_user_id_5bafbc` (`user_id`),
    KEY `idx_projects_python__5552f8` (`python_version`),
    KEY `idx_projects_venv_sc_328cdd` (`venv_scope`)
) CHARACTER SET utf8mb4 COMMENT='项目主表';
CREATE TABLE IF NOT EXISTS `project_codes` (
    `id` BIGINT NOT NULL PRIMARY KEY AUTO_INCREMENT,
    `project_id` BIGINT NOT NULL UNIQUE COMMENT '关联项目ID',
    `content` LONGTEXT NOT NULL COMMENT '代码内容',
    `language` VARCHAR(50) NOT NULL COMMENT '编程语言' DEFAULT 'python',
    `version` VARCHAR(20) NOT NULL COMMENT '版本号' DEFAULT '1.0.0',
    `content_hash` VARCHAR(64) NOT NULL COMMENT '内容哈希',
    `entry_point` VARCHAR(255) COMMENT '入口函数',
    `runtime_config` JSON COMMENT '运行时配置',
    `environment_vars` JSON COMMENT '环境变量',
    `documentation` LONGTEXT COMMENT '代码文档',
    `changelog` LONGTEXT COMMENT '变更日志'
) CHARACTER SET utf8mb4 COMMENT='代码项目详情';
CREATE TABLE IF NOT EXISTS `project_files` (
    `id` BIGINT NOT NULL PRIMARY KEY AUTO_INCREMENT,
    `project_id` BIGINT NOT NULL UNIQUE COMMENT '关联项目ID',
    `file_path` VARCHAR(500) NOT NULL COMMENT '存储路径（解压后路径或单文件路径）',
    `original_file_path` VARCHAR(500) COMMENT '原始文件路径（压缩包路径）',
    `original_name` VARCHAR(255) NOT NULL COMMENT '原始文件名',
    `file_size` BIGINT NOT NULL COMMENT '文件大小(字节)',
    `file_type` VARCHAR(50) NOT NULL COMMENT '文件类型',
    `file_hash` VARCHAR(64) NOT NULL COMMENT 'MD5哈希',
    `entry_point` VARCHAR(255) COMMENT '入口文件',
    `runtime_config` JSON COMMENT '运行时配置',
    `environment_vars` JSON COMMENT '环境变量',
    `storage_type` VARCHAR(20) NOT NULL COMMENT '存储类型' DEFAULT 'local',
    `is_compressed` BOOL NOT NULL COMMENT '是否压缩' DEFAULT 0,
    `compression_ratio` DOUBLE COMMENT '压缩比',
    `file_count` INT NOT NULL COMMENT '文件数量' DEFAULT 1,
    `additional_files` JSON COMMENT '附加文件信息（多文件上传时使用）'
) CHARACTER SET utf8mb4 COMMENT='文件项目详情';
CREATE TABLE IF NOT EXISTS `project_rules` (
    `id` BIGINT NOT NULL PRIMARY KEY AUTO_INCREMENT,
    `project_id` BIGINT NOT NULL UNIQUE COMMENT '关联项目ID',
    `engine` VARCHAR(9) NOT NULL COMMENT '采集引擎' DEFAULT 'requests',
    `target_url` VARCHAR(2000) NOT NULL COMMENT '目标URL',
    `url_pattern` VARCHAR(500) COMMENT 'URL匹配模式',
    `callback_type` VARCHAR(6) NOT NULL COMMENT '回调类型' DEFAULT 'list',
    `request_method` VARCHAR(6) NOT NULL COMMENT '请求方法' DEFAULT 'GET',
    `extraction_rules` JSON COMMENT '提取规则数组',
    `data_schema` JSON COMMENT '数据结构定义',
    `pagination_config` JSON COMMENT '分页配置JSON',
    `max_pages` INT NOT NULL COMMENT '最大页数' DEFAULT 10,
    `start_page` INT NOT NULL COMMENT '起始页码' DEFAULT 1,
    `request_delay` INT NOT NULL COMMENT '请求间隔(ms)' DEFAULT 1000,
    `retry_count` INT NOT NULL COMMENT '重试次数' DEFAULT 3,
    `timeout` INT NOT NULL COMMENT '超时时间(s)' DEFAULT 30,
    `priority` INT NOT NULL COMMENT '优先级' DEFAULT 0,
    `dont_filter` BOOL NOT NULL COMMENT '是否去重' DEFAULT 0,
    `headers` JSON COMMENT '请求头',
    `cookies` JSON COMMENT 'Cookie',
    `proxy_config` JSON COMMENT '代理配置',
    `anti_spider` JSON COMMENT '反爬虫配置',
    `task_config` JSON COMMENT '任务配置（包含task_id模板、worker_id等）'
) CHARACTER SET utf8mb4 COMMENT='规则项目详情';
CREATE TABLE IF NOT EXISTS `scheduled_tasks` (
    `id` BIGINT NOT NULL PRIMARY KEY AUTO_INCREMENT,
    `name` VARCHAR(255) NOT NULL UNIQUE COMMENT '任务名称',
    `description` LONGTEXT COMMENT '任务描述',
    `project_id` BIGINT NOT NULL COMMENT '关联项目ID',
    `task_type` VARCHAR(6) NOT NULL COMMENT '任务类型',
    `schedule_type` VARCHAR(8) NOT NULL COMMENT '调度类型',
    `cron_expression` VARCHAR(100) COMMENT 'Cron表达式',
    `interval_seconds` INT COMMENT '间隔秒数',
    `scheduled_time` DATETIME(6) COMMENT '计划执行时间',
    `max_instances` INT NOT NULL COMMENT '最大并发实例数' DEFAULT 1,
    `timeout_seconds` INT NOT NULL COMMENT '超时时间(秒)' DEFAULT 3600,
    `retry_count` INT NOT NULL COMMENT '重试次数' DEFAULT 3,
    `retry_delay` INT NOT NULL COMMENT '重试延迟(秒)' DEFAULT 60,
    `status` VARCHAR(9) NOT NULL COMMENT 'PENDING: pending\nRUNNING: running\nSUCCESS: success\nFAILED: failed\nCANCELLED: cancelled\nTIMEOUT: timeout\nPAUSED: paused' DEFAULT 'pending',
    `is_active` BOOL NOT NULL COMMENT '是否激活' DEFAULT 1,
    `last_run_time` DATETIME(6) COMMENT '最后运行时间',
    `next_run_time` DATETIME(6) COMMENT '下次运行时间',
    `failure_count` INT NOT NULL COMMENT '失败次数' DEFAULT 0,
    `success_count` INT NOT NULL COMMENT '成功次数' DEFAULT 0,
    `execution_params` JSON COMMENT '执行参数',
    `environment_vars` JSON COMMENT '环境变量',
    `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    `user_id` BIGINT NOT NULL COMMENT '创建者ID',
    KEY `idx_scheduled_t_name_a4b51d` (`name`),
    KEY `idx_scheduled_t_status_994bec` (`status`),
    KEY `idx_scheduled_t_is_acti_a43d2b` (`is_active`)
) CHARACTER SET utf8mb4 COMMENT='调度任务表';
CREATE TABLE IF NOT EXISTS `task_executions` (
    `id` BIGINT NOT NULL PRIMARY KEY AUTO_INCREMENT,
    `task_id` BIGINT NOT NULL COMMENT '关联任务ID',
    `execution_id` VARCHAR(64) NOT NULL UNIQUE COMMENT '执行ID',
    `start_time` DATETIME(6) NOT NULL COMMENT '开始时间',
    `end_time` DATETIME(6) COMMENT '结束时间',
    `duration_seconds` DOUBLE COMMENT '执行时长(秒)',
    `status` VARCHAR(9) NOT NULL COMMENT '执行状态',
    `exit_code` INT COMMENT '退出码',
    `error_message` LONGTEXT COMMENT '错误信息',
    `retry_count` INT NOT NULL COMMENT '重试次数' DEFAULT 0,
    `log_file_path` VARCHAR(512) COMMENT '日志文件路径',
    `error_log_path` VARCHAR(512) COMMENT '错误日志文件路径',
    `result_data` JSON COMMENT '结果数据',
    `cpu_usage` DOUBLE COMMENT 'CPU使用率',
    `memory_usage` BIGINT COMMENT '内存使用(bytes)',
    `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    KEY `idx_task_execut_executi_9697ff` (`execution_id`),
    KEY `idx_task_execut_status_0f7e0b` (`status`),
    KEY `idx_task_execut_start_t_14b1e5` (`start_time`)
) CHARACTER SET utf8mb4 COMMENT='任务执行记录表';
CREATE TABLE IF NOT EXISTS `users` (
    `id` INT NOT NULL PRIMARY KEY AUTO_INCREMENT COMMENT '用户ID',
    `username` VARCHAR(50) NOT NULL UNIQUE COMMENT '用户名',
    `password_hash` VARCHAR(128) NOT NULL COMMENT '密码哈希',
    `email` VARCHAR(100) COMMENT '邮箱',
    `is_active` BOOL NOT NULL COMMENT '是否激活' DEFAULT 1,
    `is_admin` BOOL NOT NULL COMMENT '是否管理员' DEFAULT 0,
    `created_at` DATETIME(6) NOT NULL COMMENT '创建时间' DEFAULT CURRENT_TIMESTAMP(6),
    `updated_at` DATETIME(6) NOT NULL COMMENT '更新时间' DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    `last_login_at` DATETIME(6) COMMENT '最后登录时间'
) CHARACTER SET utf8mb4 COMMENT='用户表';
CREATE TABLE IF NOT EXISTS `venvs` (
    `id` BIGINT NOT NULL PRIMARY KEY AUTO_INCREMENT,
    `scope` VARCHAR(7) NOT NULL COMMENT '作用域：shared/private',
    `key` VARCHAR(100) COMMENT '共享环境标识',
    `version` VARCHAR(20) NOT NULL COMMENT 'Python版本',
    `venv_path` VARCHAR(500) NOT NULL UNIQUE COMMENT '虚拟环境路径',
    `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    `created_by` BIGINT COMMENT '创建者ID',
    `interpreter_id` BIGINT NOT NULL,
    CONSTRAINT `fk_venvs_interpre_0e45728b` FOREIGN KEY (`interpreter_id`) REFERENCES `interpreters` (`id`) ON DELETE RESTRICT,
    KEY `idx_venvs_scope_9c7596` (`scope`, `key`),
    KEY `idx_venvs_version_be2e9b` (`version`)
) CHARACTER SET utf8mb4 COMMENT='虚拟环境记录（私有/共享）';
CREATE TABLE IF NOT EXISTS `project_venv_bindings` (
    `id` BIGINT NOT NULL PRIMARY KEY AUTO_INCREMENT,
    `project_id` BIGINT NOT NULL COMMENT '项目ID',
    `is_current` BOOL NOT NULL DEFAULT 1,
    `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    `created_by` BIGINT COMMENT '创建者ID',
    `venv_id` BIGINT NOT NULL,
    CONSTRAINT `fk_project__venvs_14a3dc05` FOREIGN KEY (`venv_id`) REFERENCES `venvs` (`id`) ON DELETE CASCADE,
    KEY `idx_project_ven_project_b10b50` (`project_id`, `is_current`),
    KEY `idx_project_ven_venv_id_a505d3` (`venv_id`, `is_current`)
) CHARACTER SET utf8mb4 COMMENT='项目与虚拟环境绑定（支持历史与当前）';
CREATE TABLE IF NOT EXISTS `aerich` (
    `id` INT NOT NULL PRIMARY KEY AUTO_INCREMENT,
    `version` VARCHAR(255) NOT NULL,
    `app` VARCHAR(100) NOT NULL,
    `content` JSON NOT NULL
) CHARACTER SET utf8mb4;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        """
