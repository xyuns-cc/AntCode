import type React from 'react'
import { useEffect, useState, Suspense, lazy } from 'react'
import PageContainer from '@/components/common/PageContainer'
import { Card, Descriptions, Tag, Button, Space, Skeleton } from 'antd'
import { EditOutlined, PlayCircleOutlined, ArrowLeftOutlined } from '@ant-design/icons'
import { useParams, useNavigate } from 'react-router'
import { projectService } from '@/services/projects'
import { formatDate } from '@/utils/format'
import { useThemeContext } from '@/contexts/ThemeContext'
import Logger from '@/utils/logger'
import { CodeInfoCard, FileInfoCard, RuleInfoCard, RuntimeInfoCard } from './ProjectDetailCards'
import {
  getProjectTypeText,
  getProjectStatusText,
  getProjectTypeColor,
  getProjectStatusColor
} from '@/utils/projectUtils'
import type { Project } from '@/types'

const ProjectEditDrawer = lazy(() => import('@/components/projects/ProjectEditDrawer'))

const ProjectDetail: React.FC = () => {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { isDark } = useThemeContext()
  const [project, setProject] = useState<Project | null>(null)
  const [loading, setLoading] = useState(true)
  const [editDrawerOpen, setEditDrawerOpen] = useState(false)

  // 动态样式函数
  const getCodeBlockStyle = () => ({
    background: isDark ? '#1f1f1f' : '#f5f5f5',
    color: isDark ? 'rgba(255, 255, 255, 0.88)' : 'rgba(0, 0, 0, 0.88)',
    padding: '8px',
    borderRadius: '4px',
    border: isDark ? '1px solid #434343' : '1px solid #d9d9d9'
  })

  useEffect(() => {
    const fetchProject = async () => {
      if (!id) return

      try {
        setLoading(true)
        const data = await projectService.getProject(id)
        setProject(data)
      } catch (error) {
        Logger.error('Failed to fetch project:', error)
      } finally {
        setLoading(false)
      }
    }

    fetchProject()
  }, [id])

  // 添加编辑成功的处理函数
  const handleEditSuccess = () => {
    // 重新获取项目数据
    if (id) {
      const fetchProject = async () => {
        try {
          const data = await projectService.getProject(id)
          setProject(data)
        } catch (error) {
          Logger.error('Failed to refresh project data:', error)
        }
      }
      fetchProject()
    }
  }

  if (loading) {
    return (
      <div style={{ padding: '24px' }}>
        <Card>
          <Skeleton active paragraph={{ rows: 8 }} />
        </Card>
      </div>
    )
  }

  if (!project) {
    return (
      <div style={{ textAlign: 'center', padding: '50px' }}>
        <div>项目不存在</div>
        <Button
          style={{ marginTop: '16px' }}
          onClick={() => navigate('/projects')}
        >
          返回项目列表
        </Button>
      </div>
    )
  }

  return (
    <PageContainer scrollable>
        <Card
          title={
            <Space>
              <Button
                type="text"
                icon={<ArrowLeftOutlined />}
                onClick={() => navigate('/projects')}
              >
                返回
              </Button>
              <span>{project.name}</span>
            </Space>
          }
          extra={
            <Space>
              <Button
                type="primary"
                icon={<PlayCircleOutlined />}
                onClick={() => navigate(`/tasks/create?project_id=${project.id}`)}
              >
                创建任务
              </Button>
              <Button
                icon={<EditOutlined />}
                onClick={() => setEditDrawerOpen(true)}
              >
                编辑
              </Button>
            </Space>
          }
        >
          <Descriptions column={2} bordered>
            <Descriptions.Item label="项目名称">
              {project.name}
            </Descriptions.Item>
            <Descriptions.Item label="项目类型">
              <Tag color={getProjectTypeColor(project.type)}>
                {getProjectTypeText(project.type)}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="项目状态">
              <Tag color={getProjectStatusColor(project.status)}>
                {getProjectStatusText(project.status)}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="创建时间">
              {formatDate(project.created_at)}
            </Descriptions.Item>
            <Descriptions.Item label="更新时间">
              {formatDate(project.updated_at)}
            </Descriptions.Item>
            <Descriptions.Item label="创建者">
              {project.created_by_username || `用户${project.created_by}`}
            </Descriptions.Item>
            <Descriptions.Item label="项目标签" span={2}>
              {Array.isArray(project.tags) && project.tags.length > 0 ? (
                project.tags.map((tag, index) => (
                  <Tag key={index} color="blue">
                    {tag}
                  </Tag>
                ))
              ) : (
                '无'
              )}
            </Descriptions.Item>
            <Descriptions.Item label="项目描述" span={2}>
              {project.description || '无描述'}
            </Descriptions.Item>
          </Descriptions>
        </Card>

        {/* 根据项目类型显示详情信息 */}
        {project.type === 'file' && (
          <FileInfoCard project={project} codeBlockStyle={getCodeBlockStyle()} />
        )}
        {project.type === 'rule' && (
          <RuleInfoCard project={project} codeBlockStyle={getCodeBlockStyle()} />
        )}
        {project.type === 'code' && (
          <CodeInfoCard project={project} codeBlockStyle={getCodeBlockStyle()} />
        )}

        {/* 环境信息 */}
        <RuntimeInfoCard project={project} />

      {/* 编辑抽屉 */}
      <Suspense fallback={null}>
        <ProjectEditDrawer
          open={editDrawerOpen}
          onClose={() => setEditDrawerOpen(false)}
          project={project}
          onSuccess={handleEditSuccess}
        />
      </Suspense>

    </PageContainer>
  )
}

export default ProjectDetail
