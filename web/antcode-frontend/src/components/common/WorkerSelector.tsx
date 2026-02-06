/**
 * Worker 选择器组件
 * 用于在头部选择当前操作的 Worker
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
import { useWorkerStore } from '@/stores/workerStore'
import type { Worker, WorkerStatus } from '@/types'
import type { GlobalToken } from 'antd/es/theme/interface'
import styles from './WorkerSelector.module.css'

const { Option, OptGroup } = Select

// 使用函数生成状态配置以支持主题
const getStatusConfig = (token: GlobalToken): Record<WorkerStatus, { icon: React.ReactNode; color: string; text: string }> => ({
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

// Worker 选项渲染
const WorkerOption: React.FC<{ worker: Worker; statusConfig: ReturnType<typeof getStatusConfig> }> = React.memo(({ worker, statusConfig }) => {
  const config = statusConfig[worker.status]
  
  return (
    <div className={styles.workerOption}>
      <Space size="small">
        {config.icon}
        <span className={styles.workerName}>{worker.name}</span>
        <Tag color={config.color} className={styles.statusTag}>{config.text}</Tag>
        {worker.region && (
          <Tag color="blue">{worker.region}</Tag>
        )}
      </Space>
      {worker.metrics && (
        <span className={styles.workerMetrics}>
          {worker.metrics.runningTasks}/{worker.metrics.taskCount} 任务
        </span>
      )}
    </div>
  )
})

WorkerOption.displayName = 'WorkerOption'

interface WorkerSelectorProps {
  style?: React.CSSProperties
  className?: string
}

const WorkerSelector: React.FC<WorkerSelectorProps> = ({ style, className }) => {
  const { token } = theme.useToken()
  const navigate = useNavigate()
  const { 
    currentWorker, 
    workers, 
    loading, 
    setCurrentWorker, 
    refreshWorkers 
  } = useWorkerStore()

  // 生成主题感知的状态配置
  const statusConfig = useMemo(() => getStatusConfig(token), [token])

  // 初始化加载 Worker
  useEffect(() => {
    if (workers.length === 0) {
      refreshWorkers()
    }
  }, [workers.length, refreshWorkers])

  // 按状态分组 Worker
  const onlineWorkers = workers.filter(n => n.status === 'online')
  const offlineWorkers = workers.filter(n => n.status === 'offline')
  const maintenanceWorkers = workers.filter(n => n.status === 'maintenance')
  const connectingWorkers = workers.filter(n => n.status === 'connecting')

  // 处理 Worker 选择
  const handleWorkerChange = (value: string) => {
    if (value === 'all') {
      setCurrentWorker(undefined)
    } else {
      const worker = workers.find(n => n.id === value)
      setCurrentWorker(worker)
    }
  }

  // 统计在线 Worker
  const onlineCount = onlineWorkers.length
  const totalCount = workers.length

  return (
    <div className={`${styles.container} ${className || ''}`} style={style}>
      <Select
        value={currentWorker?.id || 'all'}
        onChange={handleWorkerChange}
        className={styles.selector}
        popupClassName={styles.dropdown}
        optionLabelProp="label"
        dropdownMatchSelectWidth={false}
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
                  refreshWorkers()
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
                  navigate('/workers')
                }}
              >
                管理 Worker
              </Button>
            </div>
          </>
        )}
      >
        {/* 全部 Worker 选项 */}
        <Option value="all" label="全部 Worker">
          <div className={styles.allWorkersOption}>
            <Space>
              <GlobalOutlined style={{ color: token.colorInfo }} />
              <span>全部 Worker</span>
            </Space>
            <Badge 
              count={`${onlineCount}/${totalCount}`} 
              style={{ backgroundColor: onlineCount > 0 ? token.colorSuccess : token.colorTextDisabled }}
            />
          </div>
        </Option>

        {/* 在线 Worker */}
        {onlineWorkers.length > 0 && (
          <OptGroup label={<span><Badge status="success" /> 在线 Worker ({onlineWorkers.length})</span>}>
            {onlineWorkers.map(worker => (
              <Option key={worker.id} value={worker.id} label={worker.name}>
                <WorkerOption worker={worker} statusConfig={statusConfig} />
              </Option>
            ))}
          </OptGroup>
        )}

        {/* 连接中 Worker */}
        {connectingWorkers.length > 0 && (
          <OptGroup label={<span><Badge status="processing" /> 连接中 ({connectingWorkers.length})</span>}>
            {connectingWorkers.map(worker => (
              <Option key={worker.id} value={worker.id} label={worker.name}>
                <WorkerOption worker={worker} statusConfig={statusConfig} />
              </Option>
            ))}
          </OptGroup>
        )}

        {/* 维护中 Worker */}
        {maintenanceWorkers.length > 0 && (
          <OptGroup label={<span><Badge status="warning" /> 维护中 ({maintenanceWorkers.length})</span>}>
            {maintenanceWorkers.map(worker => (
              <Option key={worker.id} value={worker.id} label={worker.name}>
                <WorkerOption worker={worker} statusConfig={statusConfig} />
              </Option>
            ))}
          </OptGroup>
        )}

        {/* 离线 Worker */}
        {offlineWorkers.length > 0 && (
          <OptGroup label={<span><Badge status="error" /> 离线 Worker ({offlineWorkers.length})</span>}>
            {offlineWorkers.map(worker => (
              <Option key={worker.id} value={worker.id} label={worker.name} disabled>
                <WorkerOption worker={worker} statusConfig={statusConfig} />
              </Option>
            ))}
          </OptGroup>
        )}
      </Select>

      {/* 当前 Worker 状态指示器 */}
      {currentWorker && (
        <Tooltip title={`${currentWorker.name} - ${statusConfig[currentWorker.status].text}`}>
          <span className={styles.statusInfo}>
            <Badge
              status={statusConfig[currentWorker.status].color as BadgeProps['status']}
              className={styles.statusIndicator}
            />
            <span className={styles.statusText}>{statusConfig[currentWorker.status].text}</span>
          </span>
        </Tooltip>
      )}
    </div>
  )
}

export default WorkerSelector
