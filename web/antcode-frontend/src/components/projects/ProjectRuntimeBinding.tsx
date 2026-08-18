import type React from 'react'
import { Alert, Card, Descriptions, Tag } from 'antd'
import type { Project } from '@/types'

/**
 * 只读展示「这个项目会被派到哪个 Worker」。
 *
 * 运行时环境是目标 Worker 文件系统上的一个真实 venv，别的节点没有它，所以派发落点
 * 不是可以随便改的偏好——改到别的节点只会得到「运行时环境不存在」。因此这里刻意只
 * 展示不提供编辑：真要换节点，正确做法是在目标 Worker 上建好环境后重建/新建项目。
 *
 * 补这块的原因：集群加到第二个 Worker 后曾出现「项目 100% 跑不了」，而项目详情、
 * 列表、编辑抽屉里都不显示环境在哪个节点，用户没有任何线索可查。
 */
const ProjectRuntimeBinding: React.FC<{ project: Project }> = ({ project }) => {
  if (project.env_location !== 'worker' || !project.worker_env_name) return null
  const workerLabel = project.bound_worker_name || project.worker_id
  return (
    <Card title="运行时绑定" size="small" style={{ marginBottom: 16 }}>
      <Descriptions size="small" column={1} colon>
        <Descriptions.Item label="执行节点">
          {workerLabel ? <Tag color="blue">{workerLabel}</Tag> : <Tag color="red">未绑定</Tag>}
        </Descriptions.Item>
        <Descriptions.Item label="运行时环境">
          {project.worker_env_name}
          {project.python_version ? ` (Python ${project.python_version})` : ''}
        </Descriptions.Item>
      </Descriptions>
      {workerLabel ? (
        <Alert
          type="info"
          showIcon
          message="任务会被派发到该节点执行——运行时环境只存在于这台机器上。"
        />
      ) : (
        <Alert
          type="error"
          showIcon
          message="该项目没有绑定执行节点，任务会被派到任意 Worker，很可能因缺少运行时环境而失败。"
        />
      )}
    </Card>
  )
}

export default ProjectRuntimeBinding
