"""Transactional integrity guard for creating a task under a project."""

from antcode_core.domain.models.project import Project


async def lock_task_project(connection, project_id: int) -> Project:
    """Lock and return the parent Project in the caller's transaction.

    Project deletion takes the same row lock before enumerating child tasks. This
    serializes task creation with deletion without relying on an application-level
    check performed before the transaction.
    """
    project = (
        await Project.filter(id=project_id)
        .using_db(connection)
        .select_for_update()
        .only("id", "public_id", "type")
        .first()
    )
    if project is None:
        raise ValueError("项目不存在或正在删除")
    return project


__all__ = ["lock_task_project"]
