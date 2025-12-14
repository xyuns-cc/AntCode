from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS `audit_logs` (
    `id` INT NOT NULL PRIMARY KEY AUTO_INCREMENT,
    `action` VARCHAR(20) NOT NULL COMMENT '操作类型',
    `resource_type` VARCHAR(50) NOT NULL COMMENT '资源类型',
    `resource_id` VARCHAR(100) COMMENT '资源ID',
    `resource_name` VARCHAR(200) COMMENT '资源名称',
    `user_id` INT COMMENT '用户ID',
    `username` VARCHAR(100) NOT NULL COMMENT '用户名',
    `ip_address` VARCHAR(50) COMMENT 'IP地址',
    `user_agent` VARCHAR(500) COMMENT 'User-Agent',
    `description` LONGTEXT COMMENT '操作描述',
    `old_value` JSON COMMENT '修改前的值',
    `new_value` JSON COMMENT '修改后的值',
    `success` BOOL NOT NULL COMMENT '是否成功' DEFAULT 1,
    `error_message` LONGTEXT COMMENT '错误信息',
    `created_at` DATETIME(6) NOT NULL COMMENT '创建时间' DEFAULT CURRENT_TIMESTAMP(6),
    KEY `idx_audit_logs_action_4eb755` (`action`),
    KEY `idx_audit_logs_user_id_f7db5c` (`user_id`),
    KEY `idx_audit_logs_usernam_060a85` (`username`),
    KEY `idx_audit_logs_resourc_65a4f6` (`resource_type`),
    KEY `idx_audit_logs_created_bdaee3` (`created_at`)
) CHARACTER SET utf8mb4 COMMENT='审计日志';
CREATE TABLE IF NOT EXISTS `interpreters` (
    `id` BIGINT NOT NULL PRIMARY KEY AUTO_INCREMENT,
    `public_id` VARCHAR(32) NOT NULL UNIQUE,
    `tool` VARCHAR(20) NOT NULL DEFAULT 'python',
    `version` VARCHAR(20) NOT NULL,
    `install_dir` VARCHAR(500) NOT NULL,
    `python_bin` VARCHAR(500) NOT NULL,
    `status` VARCHAR(20) NOT NULL DEFAULT 'installed',
    `source` VARCHAR(5) NOT NULL COMMENT 'MISE: mise\nLOCAL: local' DEFAULT 'mise',
    `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    `created_by` BIGINT,
    UNIQUE KEY `uid_interpreter_tool_be5dc2` (`tool`, `version`, `source`),
    KEY `idx_interpreter_public__81e70a` (`public_id`),
    KEY `idx_interpreter_tool_0d1f76` (`tool`),
    KEY `idx_interpreter_status_3f1d80` (`status`),
    KEY `idx_interpreter_created_1fb8ec` (`created_at`),
    KEY `idx_interpreter_tool_0040cd` (`tool`, `status`),
    KEY `idx_interpreter_tool_563e92` (`tool`, `version`)
) CHARACTER SET utf8mb4 COMMENT='语言解释器模型';
CREATE TABLE IF NOT EXISTS `nodes` (
    `id` BIGINT NOT NULL PRIMARY KEY AUTO_INCREMENT,
    `public_id` VARCHAR(32) NOT NULL UNIQUE,
    `name` VARCHAR(100) NOT NULL UNIQUE,
    `host` VARCHAR(255) NOT NULL,
    `port` INT NOT NULL DEFAULT 8000,
    `status` VARCHAR(20) NOT NULL DEFAULT 'offline',
    `region` VARCHAR(50),
    `description` LONGTEXT,
    `tags` JSON NOT NULL,
    `version` VARCHAR(50),
    `os_type` VARCHAR(20) COMMENT '操作系统类型: Windows/Linux/Darwin',
    `os_version` VARCHAR(100) COMMENT '操作系统版本',
    `python_version` VARCHAR(20) COMMENT 'Python 版本',
    `machine_arch` VARCHAR(20) COMMENT 'CPU 架构: x86_64/arm64',
    `capabilities` JSON COMMENT '节点能力配置',
    `resource_limits` JSON COMMENT '资源限制配置',
    `metrics` JSON,
    `api_key` VARCHAR(64),
    `secret_key` VARCHAR(128),
    `last_heartbeat` DATETIME(6),
    `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    `created_by` BIGINT,
    KEY `idx_nodes_public__8be10b` (`public_id`),
    KEY `idx_nodes_name_c52c40` (`name`),
    KEY `idx_nodes_host_71c5b9` (`host`, `port`),
    KEY `idx_nodes_status_543f49` (`status`),
    KEY `idx_nodes_region_a46b97` (`region`)
) CHARACTER SET utf8mb4 COMMENT='工作节点模型';
CREATE TABLE IF NOT EXISTS `node_events` (
    `id` BIGINT NOT NULL PRIMARY KEY AUTO_INCREMENT,
    `public_id` VARCHAR(32) NOT NULL UNIQUE,
    `node_id` VARCHAR(100) NOT NULL COMMENT '节点标识',
    `event_type` VARCHAR(50) NOT NULL COMMENT '事件类型',
    `event_message` LONGTEXT,
    `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    KEY `idx_node_events_public__8f7606` (`public_id`),
    KEY `idx_node_events_node_id_feb00b` (`node_id`),
    KEY `idx_node_events_event_t_02080f` (`event_type`),
    KEY `idx_node_events_created_b90117` (`created_at`),
    KEY `idx_node_events_node_id_3e74d9` (`node_id`, `created_at`),
    KEY `idx_node_events_event_t_cadebc` (`event_type`, `created_at`)
) CHARACTER SET utf8mb4 COMMENT='节点事件日志';
CREATE TABLE IF NOT EXISTS `node_heartbeats` (
    `id` BIGINT NOT NULL PRIMARY KEY AUTO_INCREMENT,
    `public_id` VARCHAR(32) NOT NULL UNIQUE,
    `node_id` BIGINT NOT NULL,
    `metrics` JSON,
    `status` VARCHAR(20) NOT NULL,
    `timestamp` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    KEY `idx_node_heartb_public__c21700` (`public_id`),
    KEY `idx_node_heartb_node_id_d021c7` (`node_id`),
    KEY `idx_node_heartb_timesta_e62481` (`timestamp`)
) CHARACTER SET utf8mb4 COMMENT='节点心跳记录';
CREATE TABLE IF NOT EXISTS `node_performance_history` (
    `id` BIGINT NOT NULL PRIMARY KEY AUTO_INCREMENT,
    `public_id` VARCHAR(32) NOT NULL UNIQUE,
    `node_id` VARCHAR(100) NOT NULL COMMENT '节点标识',
    `timestamp` DATETIME(6) NOT NULL COMMENT '采集时间',
    `cpu_percent` DECIMAL(5,2),
    `memory_percent` DECIMAL(5,2),
    `memory_used_mb` INT,
    `disk_percent` DECIMAL(5,2),
    `network_sent_mb` DECIMAL(10,2),
    `network_recv_mb` DECIMAL(10,2),
    `uptime_seconds` BIGINT,
    `status` VARCHAR(20) NOT NULL COMMENT '节点状态' DEFAULT 'online',
    `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    KEY `idx_node_perfor_public__a86700` (`public_id`),
    KEY `idx_node_perfor_node_id_9755f2` (`node_id`),
    KEY `idx_node_perfor_timesta_ea8a48` (`timestamp`),
    KEY `idx_node_perfor_node_id_834019` (`node_id`, `timestamp`)
) CHARACTER SET utf8mb4 COMMENT='节点系统性能历史记录（按分钟聚合）';
CREATE TABLE IF NOT EXISTS `node_projects` (
    `id` BIGINT NOT NULL PRIMARY KEY AUTO_INCREMENT,
    `public_id` VARCHAR(32) NOT NULL UNIQUE,
    `node_id` BIGINT NOT NULL COMMENT '节点ID',
    `project_id` BIGINT NOT NULL COMMENT '项目ID',
    `project_public_id` VARCHAR(32) NOT NULL COMMENT '项目公开ID',
    `node_local_project_id` VARCHAR(50) COMMENT '节点本地ID',
    `file_hash` VARCHAR(64) NOT NULL COMMENT '文件hash',
    `file_size` BIGINT NOT NULL COMMENT '文件大小',
    `transfer_method` VARCHAR(20) NOT NULL COMMENT '传输方式',
    `synced_at` DATETIME(6) NOT NULL COMMENT '同步时间' DEFAULT CURRENT_TIMESTAMP(6),
    `updated_at` DATETIME(6) NOT NULL COMMENT '更新时间' DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    `status` VARCHAR(20) NOT NULL COMMENT '同步状态' DEFAULT 'synced',
    `sync_count` INT NOT NULL COMMENT '同步次数' DEFAULT 1,
    `last_used_at` DATETIME(6) COMMENT '使用时间',
    `metadata` JSON COMMENT '元数据',
    UNIQUE KEY `uid_node_projec_node_id_aa8f28` (`node_id`, `project_public_id`),
    KEY `idx_node_projec_public__3b0dba` (`public_id`),
    KEY `idx_node_projec_node_id_b240e0` (`node_id`),
    KEY `idx_node_projec_project_621dd6` (`project_id`),
    KEY `idx_node_projec_project_bdf7d8` (`project_public_id`),
    KEY `idx_node_projec_node_id_aa8f28` (`node_id`, `project_public_id`),
    KEY `idx_node_projec_node_id_be8fb6` (`node_id`, `status`),
    KEY `idx_node_projec_status_966bed` (`status`),
    KEY `idx_node_projec_synced__ae9ec1` (`synced_at`),
    KEY `idx_node_projec_last_us_e2627f` (`last_used_at`)
) CHARACTER SET utf8mb4 COMMENT='节点项目绑定 - 追踪项目版本与分发状态';
CREATE TABLE IF NOT EXISTS `node_project_files` (
    `id` BIGINT NOT NULL PRIMARY KEY AUTO_INCREMENT,
    `public_id` VARCHAR(32) NOT NULL UNIQUE,
    `node_project_id` BIGINT NOT NULL COMMENT '项目绑定ID',
    `file_path` VARCHAR(500) NOT NULL COMMENT '文件路径',
    `file_hash` VARCHAR(64) NOT NULL COMMENT '文件hash',
    `file_size` INT NOT NULL COMMENT '文件大小',
    `synced_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    UNIQUE KEY `uid_node_projec_node_pr_6cae5b` (`node_project_id`, `file_path`),
    KEY `idx_node_projec_public__f21125` (`public_id`),
    KEY `idx_node_projec_node_pr_4d6e83` (`node_project_id`),
    KEY `idx_node_projec_file_pa_f71be8` (`file_path`),
    KEY `idx_node_projec_file_ha_27aa64` (`file_hash`)
) CHARACTER SET utf8mb4 COMMENT='项目文件追踪 - 文件级增量同步';
CREATE TABLE IF NOT EXISTS `projects` (
    `id` BIGINT NOT NULL PRIMARY KEY AUTO_INCREMENT,
    `public_id` VARCHAR(32) NOT NULL UNIQUE,
    `name` VARCHAR(255) NOT NULL UNIQUE,
    `description` LONGTEXT,
    `type` VARCHAR(4) NOT NULL COMMENT 'FILE: file\nRULE: rule\nCODE: code',
    `status` VARCHAR(8) NOT NULL COMMENT 'DRAFT: draft\nACTIVE: active\nINACTIVE: inactive\nARCHIVED: archived' DEFAULT 'draft',
    `tags` JSON NOT NULL,
    `dependencies` JSON,
    `env_location` VARCHAR(10) DEFAULT 'local',
    `node_id` VARCHAR(32),
    `node_env_name` VARCHAR(100),
    `python_version` VARCHAR(20),
    `venv_scope` VARCHAR(7) COMMENT 'SHARED: shared\nPRIVATE: private',
    `venv_path` VARCHAR(500),
    `current_venv_id` BIGINT,
    `execution_strategy` VARCHAR(9) NOT NULL COMMENT '执行策略：local-本地执行, fixed-固定节点, auto-自动选择, prefer-优先绑定节点' DEFAULT 'prefer',
    `bound_node_id` BIGINT COMMENT '绑定的执行节点ID',
    `fallback_enabled` BOOL NOT NULL COMMENT '是否启用故障转移' DEFAULT 1,
    `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    `updated_by` BIGINT,
    `user_id` BIGINT NOT NULL,
    `download_count` INT NOT NULL DEFAULT 0,
    `star_count` INT NOT NULL DEFAULT 0,
    KEY `idx_projects_public__2dddc8` (`public_id`),
    KEY `idx_projects_node_id_abfbb6` (`node_id`),
    KEY `idx_projects_bound_n_ef7057` (`bound_node_id`),
    KEY `idx_projects_name_7b5b92` (`name`),
    KEY `idx_projects_type_46042a` (`type`),
    KEY `idx_projects_status_ad9f12` (`status`),
    KEY `idx_projects_user_id_5bafbc` (`user_id`),
    KEY `idx_projects_env_loc_0fba12` (`env_location`, `node_id`),
    KEY `idx_projects_python__5552f8` (`python_version`),
    KEY `idx_projects_venv_sc_328cdd` (`venv_scope`),
    KEY `idx_projects_created_f282c7` (`created_at`),
    KEY `idx_projects_updated_514ed4` (`updated_at`),
    KEY `idx_projects_current_457367` (`current_venv_id`),
    KEY `idx_projects_type_f9ede8` (`type`, `status`),
    KEY `idx_projects_user_id_8a0206` (`user_id`, `status`),
    KEY `idx_projects_status_008a43` (`status`, `created_at`),
    KEY `idx_projects_status_8cd1c6` (`status`, `updated_at`),
    KEY `idx_projects_executi_bf031d` (`execution_strategy`)
) CHARACTER SET utf8mb4 COMMENT='项目模型';
CREATE TABLE IF NOT EXISTS `project_codes` (
    `id` BIGINT NOT NULL PRIMARY KEY AUTO_INCREMENT,
    `public_id` VARCHAR(32) NOT NULL UNIQUE,
    `project_id` BIGINT NOT NULL UNIQUE,
    `content` LONGTEXT NOT NULL,
    `language` VARCHAR(50) NOT NULL DEFAULT 'python',
    `version` VARCHAR(20) NOT NULL DEFAULT '1.0.0',
    `content_hash` VARCHAR(64) NOT NULL,
    `entry_point` VARCHAR(255),
    `runtime_config` JSON,
    `environment_vars` JSON,
    `documentation` LONGTEXT,
    `changelog` LONGTEXT,
    KEY `idx_project_cod_public__818ed0` (`public_id`),
    KEY `idx_project_cod_project_27b536` (`project_id`),
    KEY `idx_project_cod_languag_96c915` (`language`),
    KEY `idx_project_cod_version_db0f22` (`version`),
    KEY `idx_project_cod_content_832c9d` (`content_hash`)
) CHARACTER SET utf8mb4 COMMENT='代码项目详情';
CREATE TABLE IF NOT EXISTS `project_files` (
    `id` BIGINT NOT NULL PRIMARY KEY AUTO_INCREMENT,
    `public_id` VARCHAR(32) NOT NULL UNIQUE,
    `project_id` BIGINT NOT NULL UNIQUE,
    `file_path` VARCHAR(500) NOT NULL,
    `original_file_path` VARCHAR(500),
    `original_name` VARCHAR(255) NOT NULL,
    `file_size` BIGINT NOT NULL,
    `file_type` VARCHAR(50) NOT NULL,
    `file_hash` VARCHAR(64) NOT NULL,
    `entry_point` VARCHAR(255),
    `runtime_config` JSON,
    `environment_vars` JSON,
    `storage_type` VARCHAR(20) NOT NULL DEFAULT 'local',
    `is_compressed` BOOL NOT NULL DEFAULT 0,
    `compression_ratio` DOUBLE,
    `file_count` INT NOT NULL DEFAULT 1,
    `additional_files` JSON,
    `is_modified` BOOL NOT NULL COMMENT '文件是否被修改' DEFAULT 0,
    `extracted_hash` VARCHAR(64) COMMENT '解压目录hash',
    `last_modified_at` DATETIME(6) COMMENT '最后修改时间',
    KEY `idx_project_fil_public__e4ba48` (`public_id`),
    KEY `idx_project_fil_project_dc3e11` (`project_id`),
    KEY `idx_project_fil_file_ty_fbab5b` (`file_type`),
    KEY `idx_project_fil_storage_d5f70e` (`storage_type`),
    KEY `idx_project_fil_is_comp_91985c` (`is_compressed`)
) CHARACTER SET utf8mb4 COMMENT='文件项目详情';
CREATE TABLE IF NOT EXISTS `project_rules` (
    `id` BIGINT NOT NULL PRIMARY KEY AUTO_INCREMENT,
    `public_id` VARCHAR(32) NOT NULL UNIQUE,
    `project_id` BIGINT NOT NULL UNIQUE,
    `engine` VARCHAR(9) NOT NULL COMMENT 'BROWSER: browser\nREQUESTS: requests\nCURL_CFFI: curl_cffi' DEFAULT 'requests',
    `target_url` VARCHAR(2000) NOT NULL,
    `url_pattern` VARCHAR(500),
    `callback_type` VARCHAR(6) NOT NULL COMMENT 'LIST: list\nDETAIL: detail\nMIXED: mixed' DEFAULT 'list',
    `request_method` VARCHAR(6) NOT NULL COMMENT 'GET: GET\nPOST: POST\nPUT: PUT\nDELETE: DELETE' DEFAULT 'GET',
    `extraction_rules` JSON,
    `data_schema` JSON,
    `pagination_config` JSON,
    `max_pages` INT NOT NULL DEFAULT 10,
    `start_page` INT NOT NULL DEFAULT 1,
    `request_delay` INT NOT NULL DEFAULT 1000,
    `retry_count` INT NOT NULL DEFAULT 3,
    `timeout` INT NOT NULL DEFAULT 30,
    `priority` INT NOT NULL DEFAULT 0,
    `dont_filter` BOOL NOT NULL DEFAULT 0,
    `headers` JSON,
    `cookies` JSON,
    `proxy_config` JSON,
    `anti_spider` JSON,
    `task_config` JSON,
    KEY `idx_project_rul_public__0b29e2` (`public_id`),
    KEY `idx_project_rul_project_6dd129` (`project_id`),
    KEY `idx_project_rul_engine_2a9879` (`engine`),
    KEY `idx_project_rul_callbac_3a0137` (`callback_type`)
) CHARACTER SET utf8mb4 COMMENT='规则项目详情';
CREATE TABLE IF NOT EXISTS `scheduled_tasks` (
    `id` BIGINT NOT NULL PRIMARY KEY AUTO_INCREMENT,
    `public_id` VARCHAR(32) NOT NULL UNIQUE,
    `name` VARCHAR(255) NOT NULL UNIQUE,
    `description` LONGTEXT,
    `project_id` BIGINT NOT NULL,
    `task_type` VARCHAR(6) NOT NULL COMMENT 'FILE: file\nCODE: code\nRULE: rule\nSPIDER: spider',
    `schedule_type` VARCHAR(8) NOT NULL COMMENT 'ONCE: once\nCRON: cron\nINTERVAL: interval\nDATE: date',
    `cron_expression` VARCHAR(100),
    `interval_seconds` INT,
    `scheduled_time` DATETIME(6),
    `max_instances` INT NOT NULL DEFAULT 1,
    `timeout_seconds` INT NOT NULL DEFAULT 3600,
    `retry_count` INT NOT NULL DEFAULT 3,
    `retry_delay` INT NOT NULL DEFAULT 60,
    `status` VARCHAR(11) NOT NULL COMMENT 'PENDING: pending\nDISPATCHING: dispatching\nQUEUED: queued\nRUNNING: running\nSUCCESS: success\nFAILED: failed\nCANCELLED: cancelled\nTIMEOUT: timeout\nPAUSED: paused' DEFAULT 'pending',
    `is_active` BOOL NOT NULL DEFAULT 1,
    `last_run_time` DATETIME(6),
    `next_run_time` DATETIME(6),
    `failure_count` INT NOT NULL DEFAULT 0,
    `success_count` INT NOT NULL DEFAULT 0,
    `execution_params` JSON,
    `environment_vars` JSON,
    `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    `user_id` BIGINT NOT NULL,
    `execution_strategy` VARCHAR(9) COMMENT '执行策略（为空则继承项目配置）',
    `specified_node_id` BIGINT COMMENT '指定执行节点ID',
    `node_id` BIGINT COMMENT '[已废弃] 所属节点ID',
    KEY `idx_scheduled_t_public__c4f498` (`public_id`),
    KEY `idx_scheduled_t_specifi_2c2e8e` (`specified_node_id`),
    KEY `idx_scheduled_t_node_id_61ae00` (`node_id`),
    KEY `idx_scheduled_t_name_a4b51d` (`name`),
    KEY `idx_scheduled_t_status_994bec` (`status`),
    KEY `idx_scheduled_t_is_acti_a43d2b` (`is_active`),
    KEY `idx_scheduled_t_user_id_7dd6d9` (`user_id`),
    KEY `idx_scheduled_t_project_1d36a0` (`project_id`),
    KEY `idx_scheduled_t_created_827f39` (`created_at`),
    KEY `idx_scheduled_t_next_ru_e00f46` (`next_run_time`),
    KEY `idx_scheduled_t_task_ty_1a4e9d` (`task_type`),
    KEY `idx_scheduled_t_is_acti_869cff` (`is_active`, `status`, `next_run_time`),
    KEY `idx_scheduled_t_status_d3c6fd` (`status`, `created_at`),
    KEY `idx_scheduled_t_user_id_10bed3` (`user_id`, `status`),
    KEY `idx_scheduled_t_project_433c5e` (`project_id`, `status`),
    KEY `idx_scheduled_t_task_ty_0a10bc` (`task_type`, `status`),
    KEY `idx_scheduled_t_node_id_2b5fff` (`node_id`, `status`),
    KEY `idx_scheduled_t_node_id_1b1f58` (`node_id`, `user_id`),
    KEY `idx_scheduled_t_executi_c6d8f7` (`execution_strategy`)
) CHARACTER SET utf8mb4 COMMENT='计划任务模型';
CREATE TABLE IF NOT EXISTS `spider_metrics_history` (
    `id` BIGINT NOT NULL PRIMARY KEY AUTO_INCREMENT,
    `public_id` VARCHAR(32) NOT NULL UNIQUE,
    `node_id` VARCHAR(100) NOT NULL COMMENT '节点标识',
    `timestamp` DATETIME(6) NOT NULL COMMENT '采集时间',
    `tasks_total` INT NOT NULL DEFAULT 0,
    `tasks_success` INT NOT NULL DEFAULT 0,
    `tasks_failed` INT NOT NULL DEFAULT 0,
    `tasks_running` INT NOT NULL DEFAULT 0,
    `pages_crawled` INT NOT NULL DEFAULT 0,
    `items_scraped` INT NOT NULL DEFAULT 0,
    `requests_total` INT NOT NULL DEFAULT 0,
    `requests_failed` INT NOT NULL DEFAULT 0,
    `avg_response_time_ms` INT NOT NULL DEFAULT 0,
    `error_timeout` INT NOT NULL DEFAULT 0,
    `error_network` INT NOT NULL DEFAULT 0,
    `error_parse` INT NOT NULL DEFAULT 0,
    `error_other` INT NOT NULL DEFAULT 0,
    `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    KEY `idx_spider_metr_public__13fb2e` (`public_id`),
    KEY `idx_spider_metr_node_id_96b6d5` (`node_id`),
    KEY `idx_spider_metr_timesta_a01a0e` (`timestamp`),
    KEY `idx_spider_metr_node_id_e4f459` (`node_id`, `timestamp`)
) CHARACTER SET utf8mb4 COMMENT='爬虫业务指标历史记录（按分钟聚合）';
CREATE TABLE IF NOT EXISTS `system_configs` (
    `id` BIGINT NOT NULL PRIMARY KEY AUTO_INCREMENT,
    `public_id` VARCHAR(32) NOT NULL UNIQUE,
    `config_key` VARCHAR(100) NOT NULL UNIQUE,
    `config_value` LONGTEXT NOT NULL,
    `category` VARCHAR(50) NOT NULL,
    `description` LONGTEXT,
    `value_type` VARCHAR(20) NOT NULL DEFAULT 'string',
    `is_active` BOOL NOT NULL DEFAULT 1,
    `modified_by` VARCHAR(50),
    `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    KEY `idx_system_conf_public__30d519` (`public_id`),
    KEY `idx_system_conf_config__d6feea` (`config_key`),
    KEY `idx_system_conf_categor_35760e` (`category`),
    KEY `idx_system_conf_is_acti_b1388d` (`is_active`),
    KEY `idx_system_conf_categor_c03804` (`category`, `is_active`)
) CHARACTER SET utf8mb4 COMMENT='系统配置模型 - 用于存储动态配置项';
CREATE TABLE IF NOT EXISTS `task_executions` (
    `id` BIGINT NOT NULL PRIMARY KEY AUTO_INCREMENT,
    `public_id` VARCHAR(32) NOT NULL UNIQUE,
    `task_id` BIGINT NOT NULL,
    `execution_id` VARCHAR(64) NOT NULL UNIQUE,
    `start_time` DATETIME(6) NOT NULL,
    `end_time` DATETIME(6),
    `duration_seconds` DOUBLE,
    `status` VARCHAR(11) NOT NULL COMMENT 'PENDING: pending\nDISPATCHING: dispatching\nQUEUED: queued\nRUNNING: running\nSUCCESS: success\nFAILED: failed\nCANCELLED: cancelled\nTIMEOUT: timeout\nPAUSED: paused',
    `exit_code` INT,
    `error_message` LONGTEXT,
    `retry_count` INT NOT NULL DEFAULT 0,
    `log_file_path` VARCHAR(512),
    `error_log_path` VARCHAR(512),
    `result_data` JSON,
    `cpu_usage` DOUBLE,
    `memory_usage` BIGINT,
    `last_heartbeat` DATETIME(6) COMMENT '最后心跳时间',
    `node_id` BIGINT COMMENT '执行节点ID',
    `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    KEY `idx_task_execut_public__62e4eb` (`public_id`),
    KEY `idx_task_execut_node_id_1f5043` (`node_id`),
    KEY `idx_task_execut_executi_9697ff` (`execution_id`),
    KEY `idx_task_execut_task_id_f940a2` (`task_id`),
    KEY `idx_task_execut_status_0f7e0b` (`status`),
    KEY `idx_task_execut_start_t_14b1e5` (`start_time`),
    KEY `idx_task_execut_created_afc0d3` (`created_at`),
    KEY `idx_task_execut_task_id_73f704` (`task_id`, `status`),
    KEY `idx_task_execut_task_id_741048` (`task_id`, `start_time`),
    KEY `idx_task_execut_status_2f0717` (`status`, `start_time`),
    KEY `idx_task_execut_status_46f876` (`status`, `last_heartbeat`)
) CHARACTER SET utf8mb4 COMMENT='任务执行记录';
CREATE TABLE IF NOT EXISTS `users` (
    `id` BIGINT NOT NULL PRIMARY KEY AUTO_INCREMENT,
    `public_id` VARCHAR(32) NOT NULL UNIQUE,
    `username` VARCHAR(50) NOT NULL UNIQUE,
    `password_hash` VARCHAR(128) NOT NULL,
    `email` VARCHAR(100),
    `is_active` BOOL NOT NULL DEFAULT 1,
    `is_admin` BOOL NOT NULL DEFAULT 0,
    `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    `last_login_at` DATETIME(6),
    KEY `idx_users_public__1c65a4` (`public_id`),
    KEY `idx_users_usernam_266d85` (`username`),
    KEY `idx_users_email_133a6f` (`email`),
    KEY `idx_users_is_acti_7e4021` (`is_active`),
    KEY `idx_users_is_admi_70b49f` (`is_admin`),
    KEY `idx_users_last_lo_728870` (`last_login_at`),
    KEY `idx_users_is_acti_9e662e` (`is_active`, `is_admin`)
) CHARACTER SET utf8mb4 COMMENT='用户模型';
CREATE TABLE IF NOT EXISTS `user_node_permissions` (
    `id` BIGINT NOT NULL PRIMARY KEY AUTO_INCREMENT,
    `public_id` VARCHAR(32) NOT NULL UNIQUE,
    `user_id` BIGINT NOT NULL COMMENT '用户ID',
    `node_id` BIGINT NOT NULL COMMENT '节点ID',
    `permission` VARCHAR(20) NOT NULL COMMENT '权限级别' DEFAULT 'use',
    `assigned_by` BIGINT COMMENT '分配者ID（管理员）',
    `assigned_at` DATETIME(6) NOT NULL COMMENT '分配时间' DEFAULT CURRENT_TIMESTAMP(6),
    `note` LONGTEXT COMMENT '备注说明',
    UNIQUE KEY `uid_user_node_p_user_id_8b9133` (`user_id`, `node_id`),
    KEY `idx_user_node_p_public__6bee0b` (`public_id`),
    KEY `idx_user_node_p_user_id_3150be` (`user_id`),
    KEY `idx_user_node_p_node_id_9bd4df` (`node_id`)
) CHARACTER SET utf8mb4 COMMENT='用户节点权限 - 记录用户可以访问哪些节点';
CREATE TABLE IF NOT EXISTS `venvs` (
    `id` BIGINT NOT NULL PRIMARY KEY AUTO_INCREMENT,
    `public_id` VARCHAR(32) NOT NULL UNIQUE,
    `scope` VARCHAR(7) NOT NULL COMMENT 'SHARED: shared\nPRIVATE: private',
    `key` VARCHAR(100),
    `version` VARCHAR(20) NOT NULL,
    `venv_path` VARCHAR(500) NOT NULL UNIQUE,
    `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    `created_by` BIGINT,
    `node_id` BIGINT COMMENT '所属节点ID',
    `interpreter_id` BIGINT NOT NULL,
    CONSTRAINT `fk_venvs_interpre_0e45728b` FOREIGN KEY (`interpreter_id`) REFERENCES `interpreters` (`id`) ON DELETE RESTRICT,
    KEY `idx_venvs_public__d1fb03` (`public_id`),
    KEY `idx_venvs_node_id_12560f` (`node_id`),
    KEY `idx_venvs_scope_9c7596` (`scope`, `key`),
    KEY `idx_venvs_version_be2e9b` (`version`),
    KEY `idx_venvs_created_109e7e` (`created_at`),
    KEY `idx_venvs_created_ab0203` (`created_by`),
    KEY `idx_venvs_scope_fecab2` (`scope`, `version`),
    KEY `idx_venvs_node_id_f265bb` (`node_id`, `scope`)
) CHARACTER SET utf8mb4 COMMENT='虚拟环境模型';
CREATE TABLE IF NOT EXISTS `project_venv_bindings` (
    `id` BIGINT NOT NULL PRIMARY KEY AUTO_INCREMENT,
    `public_id` VARCHAR(32) NOT NULL UNIQUE,
    `project_id` BIGINT NOT NULL,
    `is_current` BOOL NOT NULL DEFAULT 1,
    `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    `created_by` BIGINT,
    `venv_id` BIGINT NOT NULL,
    CONSTRAINT `fk_project__venvs_14a3dc05` FOREIGN KEY (`venv_id`) REFERENCES `venvs` (`id`) ON DELETE CASCADE,
    KEY `idx_project_ven_public__6860a9` (`public_id`),
    KEY `idx_project_ven_project_b10b50` (`project_id`, `is_current`),
    KEY `idx_project_ven_venv_id_a505d3` (`venv_id`, `is_current`),
    KEY `idx_project_ven_created_05a81f` (`created_at`),
    KEY `idx_project_ven_created_c7a542` (`created_by`)
) CHARACTER SET utf8mb4 COMMENT='项目与虚拟环境绑定';
CREATE TABLE IF NOT EXISTS `aerich` (
    `id` INT NOT NULL PRIMARY KEY AUTO_INCREMENT,
    `version` VARCHAR(255) NOT NULL,
    `app` VARCHAR(100) NOT NULL,
    `content` JSON NOT NULL
) CHARACTER SET utf8mb4;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        """


MODELS_STATE = (
    "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"
    "BRuqE/RZLG6tYkOljqtf/w8So/Q+"
)
