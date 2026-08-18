/**
 * 项目删除确认文案。
 *
 * 原文案只有一句"此操作不可恢复"，而后端 `delete_project_cascade` 实际会跨 6 张表
 * 级联清理并连 Redis 里已抓到的数据一起删。用户在点「删除」前完全不知道会连带
 * 毁掉任务、执行记录和爬取批次——走查实测删掉 1 个项目同时带走了 1 个任务、
 * 1 次成功执行（含真实抓取数据）和 1 个爬取批次。
 *
 * 清单与后端 `project_cascade_delete.py` 的 `deleted` 计数字段一一对应，改后端
 * 级联范围时这里要同步。
 */
import type React from 'react'
import { Alert, Typography } from 'antd'
import type { Project } from '@/types'

const { Text } = Typography

const CASCADE_ITEMS = [
  '该项目下的全部任务及其调度',
  '这些任务的全部执行记录与执行日志',
  '全部爬取批次，以及批次已抓到的数据',
  '项目配置（规则 / 文件 / 代码）、运行时绑定与源码快照'
]

interface ProjectDeleteWarningProps {
  project: Project | null
}

const ProjectDeleteWarning: React.FC<ProjectDeleteWarningProps> = ({ project }) => {
  if (!project) return null

  return (
    <>
      <p>
        确定要删除项目 &quot;<Text strong>{project.name}</Text>&quot; 吗？
        该项目当前关联 <Text strong>{project.task_count ?? 0}</Text> 个任务。
      </p>
      <Alert
        type="warning"
        showIcon
        message="以下数据会被一并永久删除，且无法恢复"
        description={
          <ul style={{ margin: 0, paddingInlineStart: 20 }}>
            {CASCADE_ITEMS.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        }
      />
    </>
  )
}

export default ProjectDeleteWarning
