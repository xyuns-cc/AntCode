from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS `node_events` (
    `id` BIGINT NOT NULL PRIMARY KEY AUTO_INCREMENT,
    `node_id` VARCHAR(100) NOT NULL COMMENT '节点标识',
    `event_type` VARCHAR(50) NOT NULL COMMENT '事件类型',
    `event_message` LONGTEXT,
    `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    KEY `idx_node_events_node_id_feb00b` (`node_id`),
    KEY `idx_node_events_event_t_02080f` (`event_type`),
    KEY `idx_node_events_created_b90117` (`created_at`),
    KEY `idx_node_events_node_id_3e74d9` (`node_id`, `created_at`),
    KEY `idx_node_events_event_t_cadebc` (`event_type`, `created_at`)
) CHARACTER SET utf8mb4 COMMENT='节点事件日志';
        CREATE TABLE IF NOT EXISTS `node_performance_history` (
    `id` BIGINT NOT NULL PRIMARY KEY AUTO_INCREMENT,
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
    KEY `idx_node_perfor_node_id_9755f2` (`node_id`),
    KEY `idx_node_perfor_timesta_ea8a48` (`timestamp`),
    KEY `idx_node_perfor_node_id_834019` (`node_id`, `timestamp`)
) CHARACTER SET utf8mb4 COMMENT='节点系统性能历史记录（按分钟聚合）';
        CREATE TABLE IF NOT EXISTS `spider_metrics_history` (
    `id` BIGINT NOT NULL PRIMARY KEY AUTO_INCREMENT,
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
    KEY `idx_spider_metr_node_id_96b6d5` (`node_id`),
    KEY `idx_spider_metr_timesta_a01a0e` (`timestamp`),
    KEY `idx_spider_metr_node_id_e4f459` (`node_id`, `timestamp`)
) CHARACTER SET utf8mb4 COMMENT='爬虫业务指标历史记录（按分钟聚合）';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS `node_performance_history`;
        DROP TABLE IF EXISTS `node_events`;
        DROP TABLE IF EXISTS `spider_metrics_history`;"""
