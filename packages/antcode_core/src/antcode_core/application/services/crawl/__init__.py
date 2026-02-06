"""分布式爬虫队列服务"""

from antcode_core.application.services.crawl.batch_service import (
    VALID_STATE_TRANSITIONS,
    CrawlBatchService,
    crawl_batch_service,
)
from antcode_core.application.services.crawl.dedup_service import CrawlDedupService, crawl_dedup_service
from antcode_core.application.services.crawl.election_service import (
    MasterElectionService,
    create_election_service,
)
from antcode_core.application.services.crawl.metrics_service import (
    Alert,
    AlertConfig,
    BatchMetrics,
    CrawlMetricsService,
    SystemMetrics,
    crawl_metrics_service,
    create_metrics_service,
)
from antcode_core.application.services.crawl.progress_service import (
    BatchProgress,
    Checkpoint,
    CrawlProgressService,
    crawl_progress_service,
)
from antcode_core.application.services.crawl.queue_service import (
    CrawlQueueService,
    CrawlTask,
    DequeueResult,
    EnqueueResult,
    InvalidStatusTransitionError,
    TaskStatusError,
    TaskStatusTransition,
    crawl_queue_service,
)
from antcode_core.application.services.crawl.test_service import (
    CrawlTestConfig,
    CrawlTestResult,
    CrawlTestService,
    crawl_test_service,
)
from antcode_core.application.services.crawl.worker_service import (
    WorkerInfo,
    WorkerRegistryService,
    create_worker_registry_service,
    worker_registry_service,
)

__all__ = [
    "CrawlDedupService",
    "crawl_dedup_service",
    "CrawlQueueService",
    "CrawlTask",
    "EnqueueResult",
    "DequeueResult",
    "TaskStatusTransition",
    "TaskStatusError",
    "InvalidStatusTransitionError",
    "crawl_queue_service",
    "CrawlProgressService",
    "BatchProgress",
    "Checkpoint",
    "crawl_progress_service",
    "CrawlBatchService",
    "crawl_batch_service",
    "VALID_STATE_TRANSITIONS",
    "CrawlTestService",
    "CrawlTestResult",
    "CrawlTestConfig",
    "crawl_test_service",
    "MasterElectionService",
    "create_election_service",
    "WorkerRegistryService",
    "WorkerInfo",
    "worker_registry_service",
    "create_worker_registry_service",
    "CrawlMetricsService",
    "SystemMetrics",
    "BatchMetrics",
    "AlertConfig",
    "Alert",
    "crawl_metrics_service",
    "create_metrics_service",
]
