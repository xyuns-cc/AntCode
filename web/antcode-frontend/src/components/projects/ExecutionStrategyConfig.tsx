import React, { useEffect, useState } from 'react'
import { Form, Select, Switch, Space, Tag, Tooltip, Alert, Typography } from 'antd'
import {
  CloudServerOutlined,
  DesktopOutlined,
  ThunderboltOutlined,
  SafetyOutlined,
  QuestionCircleOutlined,
} from '@ant-design/icons'
import type { ExecutionStrategy } from '@/types/project'
import type { Node } from '@/types/node'
import nodeService from '@/services/nodes'

const { Text } = Typography

interface ExecutionStrategyConfigProps {
  /** 当前执行策略 */
  value?: {
    execution_strategy?: ExecutionStrategy
    bound_node_id?: string
    fallback_enabled?: boolean
  }
  /** 值变化回调 */
  onChange?: (value: {
    execution_strategy: ExecutionStrategy
    bound_node_id?: string
    fallback_enabled?: boolean
  }) => void
  /** 是否禁用 */
  disabled?: boolean
  /** 是否为任务级别配置（显示"继承项目"选项） */
  isTaskLevel?: boolean
  /** 项目的执行策略（任务级别时显示） */
  projectStrategy?: {
    execution_strategy?: ExecutionStrategy
    bound_node_id?: string
    bound_node_name?: string
  }
}

const STRATEGY_OPTIONS = [
  {
    value: 'local',
    label: '本地执行',
    icon: <DesktopOutlined />,
    description: '在主节点本地执行任务',
    color: 'blue',
  },
  {
    value: 'fixed',
    label: '固定节点',
    icon: <SafetyOutlined />,
    description: '仅在绑定节点执行，不可用时任务失败',
    color: 'red',
  },
  {
    value: 'auto',
    label: '自动选择',
    icon: <ThunderboltOutlined />,
    description: '根据负载自动选择最优节点',
    color: 'green',
  },
  {
    value: 'prefer',
    label: '优先绑定节点',
    icon: <CloudServerOutlined />,
    description: '优先使用绑定节点，不可用时自动选择其他节点',
    color: 'orange',
  },
]

const ExecutionStrategyConfig: React.FC<ExecutionStrategyConfigProps> = ({
  value,
  onChange,
  disabled = false,
  isTaskLevel = false,
  projectStrategy,
}) => {
  const [nodes, setNodes] = useState<Node[]>([])
  const [loading, setLoading] = useState(false)

  const strategy = value?.execution_strategy || 'prefer'
  const boundNodeId = value?.bound_node_id
  const fallbackEnabled = value?.fallback_enabled ?? true

  // 加载节点列表
  useEffect(() => {
    const loadNodes = async () => {
      setLoading(true)
      try {
        const nodeList = await nodeService.getAllNodes()
        setNodes(nodeList.filter(n => n.status === 'online'))
      } catch (error) {
        console.error('加载节点列表失败:', error)
      } finally {
        setLoading(false)
      }
    }
    loadNodes()
  }, [])

  const handleStrategyChange = (newStrategy: ExecutionStrategy) => {
    onChange?.({
      execution_strategy: newStrategy,
      bound_node_id: ['fixed', 'prefer'].includes(newStrategy) ? boundNodeId : undefined,
      fallback_enabled: newStrategy === 'prefer' ? fallbackEnabled : undefined,
    })
  }

  const handleNodeChange = (nodeId: string) => {
    onChange?.({
      execution_strategy: strategy,
      bound_node_id: nodeId,
      fallback_enabled,
    })
  }

  const handleFallbackChange = (enabled: boolean) => {
    onChange?.({
      execution_strategy: strategy,
      bound_node_id: boundNodeId,
      fallback_enabled: enabled,
    })
  }

  const needsNodeSelection = ['fixed', 'prefer'].includes(strategy)
  const showFallbackSwitch = strategy === 'prefer'

  const currentOption = STRATEGY_OPTIONS.find(opt => opt.value === strategy)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* 任务级别时显示项目配置提示 */}
      {isTaskLevel && projectStrategy && (
        <Alert
          type="info"
          showIcon
          message={
            <Space>
              <Text>项目执行策略：</Text>
              <Tag color={STRATEGY_OPTIONS.find(o => o.value === projectStrategy.execution_strategy)?.color}>
                {STRATEGY_OPTIONS.find(o => o.value === projectStrategy.execution_strategy)?.label || '本地执行'}
              </Tag>
              {projectStrategy.bound_node_name && (
                <Text type="secondary">绑定节点: {projectStrategy.bound_node_name}</Text>
              )}
            </Space>
          }
        />
      )}

      {/* 执行策略选择 */}
      <Form.Item
        label={
          <Space>
            执行策略
            <Tooltip title="决定任务在哪个节点上执行">
              <QuestionCircleOutlined style={{ color: '#999' }} />
            </Tooltip>
          </Space>
        }
        style={{ marginBottom: 0 }}
      >
        <Select
          value={strategy}
          onChange={handleStrategyChange}
          disabled={disabled}
          style={{ width: '100%' }}
          optionLabelProp="label"
        >
          {isTaskLevel && (
            <Select.Option value="" label="继承项目配置">
              <Space>
                <Tag color="default">继承</Tag>
                <span>继承项目配置</span>
              </Space>
            </Select.Option>
          )}
          {STRATEGY_OPTIONS.map(option => (
            <Select.Option key={option.value} value={option.value} label={option.label}>
              <Space>
                <Tag color={option.color} icon={option.icon}>
                  {option.label}
                </Tag>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {option.description}
                </Text>
              </Space>
            </Select.Option>
          ))}
        </Select>
      </Form.Item>

      {/* 策略说明 */}
      {currentOption && (
        <Alert
          type="info"
          showIcon={false}
          message={
            <Space>
              {currentOption.icon}
              <Text type="secondary">{currentOption.description}</Text>
            </Space>
          }
          style={{ padding: '8px 12px' }}
        />
      )}

      {/* 节点选择（fixed/prefer 策略时显示） */}
      {needsNodeSelection && (
        <Form.Item
          label={
            <Space>
              {strategy === 'fixed' ? '执行节点' : '绑定节点'}
              <Tooltip title={strategy === 'fixed' ? '任务将固定在此节点执行' : '优先在此节点执行'}>
                <QuestionCircleOutlined style={{ color: '#999' }} />
              </Tooltip>
            </Space>
          }
          required={strategy === 'fixed'}
          style={{ marginBottom: 0 }}
        >
          <Select
            value={boundNodeId}
            onChange={handleNodeChange}
            disabled={disabled}
            loading={loading}
            placeholder="选择节点"
            allowClear={strategy !== 'fixed'}
            style={{ width: '100%' }}
            notFoundContent={nodes.length === 0 ? '暂无在线节点' : undefined}
          >
            {nodes.map(node => (
              <Select.Option key={node.id} value={node.id}>
                <Space>
                  <CloudServerOutlined />
                  <span>{node.name}</span>
                  <Tag color="green" style={{ marginLeft: 8 }}>在线</Tag>
                  {node.region && <Text type="secondary">({node.region})</Text>}
                </Space>
              </Select.Option>
            ))}
          </Select>
        </Form.Item>
      )}

      {/* 故障转移开关（prefer 策略时显示） */}
      {showFallbackSwitch && (
        <Form.Item
          label={
            <Space>
              故障转移
              <Tooltip title="当绑定节点不可用时，自动选择其他可用节点执行">
                <QuestionCircleOutlined style={{ color: '#999' }} />
              </Tooltip>
            </Space>
          }
          style={{ marginBottom: 0 }}
        >
          <Switch
            checked={fallbackEnabled}
            onChange={handleFallbackChange}
            disabled={disabled}
            checkedChildren="启用"
            unCheckedChildren="禁用"
          />
          <Text type="secondary" style={{ marginLeft: 12 }}>
            {fallbackEnabled
              ? '绑定节点不可用时自动选择其他节点'
              : '绑定节点不可用时任务将失败'}
          </Text>
        </Form.Item>
      )}
    </div>
  )
}

export default ExecutionStrategyConfig
