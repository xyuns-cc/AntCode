"""
AntCode Master 调度服务

Schedule Plane - 负责任务调度、Worker 选择、状态协调
"""

__version__ = "0.1.0"

from antcode_master.leader import ensure_leader, get_fencing_token, leader_election

__all__ = ["leader_election", "ensure_leader", "get_fencing_token"]
