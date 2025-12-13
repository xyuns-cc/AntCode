/**
 * 节点选择器组件
 * 用于在头部选择当前操作的节点
 */
import React, { useEffect, useMemo } from 'react'
import { Select, Space, Badge, Tag, Divider, Button, Tooltip, theme } from 'antd'
import type { BadgeProps } from 'antd'
import {
  GlobalOutlined,
  SettingOutlined,
  ReloadOutlined,
  CheckCircleFilled,
  CloseCircleFilled,
  MinusCircleFilled,
  LoadingOutlined
} from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { useNodeStore } from '@/stores/nodeStore'
import type { Node, NodeStatus } from '@/types'
import type { GlobalToken } from 'antd/es/theme/interface'
import styles from './NodeSelector.module.css'

const { Option, OptGroup } = Select

// 使用函数生成状态配置以支持主题
const getStatusConfig = (token: GlobalToken): Record<NodeStatus, { icon: React.ReactNode; color: string; text: string }> => ({
  online: {
    icon: <CheckCircleFilled style={{ color: token.colorSuccess }} />,
    color: 'success',
    text: '在线'
  },
  offline: {
    icon: <CloseCircleFilled style={{ color: token.colorError }} />,
    color: 'error',
    text: '离线'
  },
  maintenance: {
    icon: <MinusCircleFilled style={{ color: token.colorWarning }} />,
    color: 'warning',
    text: '维护中'
  },
  connecting: {
    icon: <LoadingOutlined style={{ color: token.colorInfo }} />,
    color: 'processing',
    text: '连接中'
  }
})

// 节点选项渲染
const NodeOption: React.FC<{ node: Node; statusConfig: ReturnType<typeof getStatusConfig> }> = React.memo(({ node, statusConfig }) => {
  const config = statusConfig[node.status]
  
  return (
    <div className={styles.nodeOption}>
      <Space size="small">
        {config.icon}
        <span className={styles.nodeName}>{node.name}</span>
        {node.region && (
          <Tag size="small" color="blue">{node.region}</Tag>
        )}
      </Space>
      {node.metrics && (
        <span className={styles.nodeMetrics}>
          {node.metrics.runningTasks}/{node.metrics.taskCount} 任务
        </span>
      )}
    </div>
  )
})

NodeOption.displayName = 'NodeOption'

interface NodeSelectorProps {
  style?: React.CSSProperties
  className?: string
}

const NodeSelector: React.FC<NodeSelectorProps> = ({ style, className }) => {
  const { token } = theme.useToken()
  const navigate = useNavigate()
  const { 
    currentNode, 
    nodes, 
    loading, 
    setCurrentNode, 
    refreshNodes 
  } = useNodeStore()

  // 生成主题感知的状态配置
  const statusConfig = useMemo(() => getStatusConfig(token), [token])

  // 初始化加载节点
  useEffect(() => {
    if (nodes.length === 0) {
      refreshNodes()
    }
  }, [nodes.length, refreshNodes])

  // 按状态分组节点
  const onlineNodes = nodes.filter(n => n.status === 'online')
  const offlineNodes = nodes.filter(n => n.status === 'offline')
  const maintenanceNodes = nodes.filter(n => n.status === 'maintenance')
  const connectingNodes = nodes.filter(n => n.status === 'connecting')

  // 处理节点选择
  const handleNodeChange = (value: string) => {
    if (value === 'all') {
      setCurrentNode(null)
    } else {
      const node = nodes.find(n => n.id === value)
      setCurrentNode(node || null)
    }
  }

  // 统计在线节点
  const onlineCount = onlineNodes.length
  const totalCount = nodes.length

  return (
    <div className={`${styles.container} ${className || ''}`} style={style}>
      <Select
        value={currentNode?.id || 'all'}
        onChange={handleNodeChange}
        className={styles.selector}
        popupClassName={styles.dropdown}
        loading={loading}
        suffixIcon={loading ? <LoadingOutlined /> : undefined}
        dropdownRender={(menu) => (
          <>
            {menu}
            <Divider style={{ margin: '8px 0' }} />
            <div className={styles.dropdownFooter}>
              <Button 
                type="text" 
                size="small"
                icon={<ReloadOutlined />}
                onClick={(e) => {
                  e.stopPropagation()
                  refreshNodes()
                }}
                loading={loading}
              >
                刷新
              </Button>
              <Button 
                type="text" 
                size="small"
                icon={<SettingOutlined />}
                onClick={(e) => {
                  e.stopPropagation()
                  navigate('/nodes')
                }}
              >
                管理节点
              </Button>
            </div>
          </>
        )}
      >
        {/* 全部节点选项 */}
        <Option value="all">
          <div className={styles.allNodesOption}>
            <Space>
              <GlobalOutlined style={{ color: token.colorInfo }} />
              <span>全部节点</span>
            </Space>
            <Badge 
              count={`${onlineCount}/${totalCount}`} 
              style={{ backgroundColor: onlineCount > 0 ? token.colorSuccess : token.colorTextDisabled }}
            />
          </div>
        </Option>

        {/* 在线节点 */}
        {onlineNodes.length > 0 && (
          <OptGroup label={<span><Badge status="success" /> 在线节点 ({onlineNodes.length})</span>}>
            {onlineNodes.map(node => (
              <Option key={node.id} value={node.id}>
                <NodeOption node={node} statusConfig={statusConfig} />
              </Option>
            ))}
          </OptGroup>
        )}

        {/* 连接中节点 */}
        {connectingNodes.length > 0 && (
          <OptGroup label={<span><Badge status="processing" /> 连接中 ({connectingNodes.length})</span>}>
            {connectingNodes.map(node => (
              <Option key={node.id} value={node.id}>
                <NodeOption node={node} statusConfig={statusConfig} />
              </Option>
            ))}
          </OptGroup>
        )}

        {/* 维护中节点 */}
        {maintenanceNodes.length > 0 && (
          <OptGroup label={<span><Badge status="warning" /> 维护中 ({maintenanceNodes.length})</span>}>
            {maintenanceNodes.map(node => (
              <Option key={node.id} value={node.id}>
                <NodeOption node={node} statusConfig={statusConfig} />
              </Option>
            ))}
          </OptGroup>
        )}

        {/* 离线节点 */}
        {offlineNodes.length > 0 && (
          <OptGroup label={<span><Badge status="error" /> 离线节点 ({offlineNodes.length})</span>}>
            {offlineNodes.map(node => (
              <Option key={node.id} value={node.id} disabled>
                <NodeOption node={node} statusConfig={statusConfig} />
              </Option>
            ))}
          </OptGroup>
        )}
      </Select>

      {/* 当前节点状态指示器 */}
      {currentNode && (
        <Tooltip title={`${currentNode.name} - ${statusConfig[currentNode.status].text}`}>
          <span style={{ display: 'inline-flex', alignItems: 'center' }}>
            <Badge
              status={statusConfig[currentNode.status].color as BadgeProps['status']}
              className={styles.statusIndicator}
            />
          </span>
        </Tooltip>
      )}
    </div>
  )
}

export default NodeSelector
