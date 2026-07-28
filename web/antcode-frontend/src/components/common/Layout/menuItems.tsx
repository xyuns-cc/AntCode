import {
  BellOutlined,
  BranchesOutlined,
  ClusterOutlined,
  CodeOutlined,
  DashboardOutlined,
  DatabaseOutlined,
  FileTextOutlined,
  PlayCircleOutlined,
  ProjectOutlined,
  SettingOutlined,
  TeamOutlined,
} from '@ant-design/icons'
import type { MenuItem, User } from '@/types'

export const createMenuItems = (user: User | null): MenuItem[] => [
  { key: '/dashboard', label: '仪表板', icon: <DashboardOutlined />, path: '/dashboard' },
  { key: '/workers', label: 'Worker 管理', icon: <ClusterOutlined />, path: '/workers', hidden: !user?.is_admin },
  { key: '/envs', label: '环境管理', icon: <CodeOutlined />, path: '/envs' },
  { key: '/projects', label: '项目管理', icon: <ProjectOutlined />, path: '/projects' },
  { key: '/repositories', label: '代码仓库', icon: <BranchesOutlined />, path: '/repositories' },
  { key: '/tasks', label: '任务管理', icon: <PlayCircleOutlined />, path: '/tasks' },
  { key: '/crawl-batches', label: '爬取批次', icon: <DatabaseOutlined />, path: '/crawl-batches' },
  { key: '/user-management', label: '用户管理', icon: <TeamOutlined />, path: '/user-management', hidden: !user?.is_admin },
  { key: '/alert-config', label: '告警配置', icon: <BellOutlined />, path: '/alert-config', hidden: !user?.is_admin },
  { key: '/audit-log', label: '审计日志', icon: <FileTextOutlined />, path: '/audit-log', hidden: !user?.is_admin },
  { key: '/system-config', label: '系统配置', icon: <SettingOutlined />, path: '/system-config', hidden: user?.role !== 'super_admin' },
]
