import type React from 'react'
import { useEffect, useState } from 'react'
import { Form, Select, Space, Tag, Tooltip, Alert, Typography, Spin } from 'antd'
import {
  GlobalOutlined,
  CloudServerOutlined,
  QuestionCircleOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'

import { workerService } from '@/services/workers'
import Logger from '@/utils/logger'

const { Text } = Typography

interface RegionWorkerSelectorProps {
  value?: {
    region?: string
    require_render?: boolean
  }
  onChange?: (value: {
    region?: string
    require_render?: boolean
  }) => void
  disabled?: boolean
  /** 是否需要浏览器渲染能力，会影响可选 worker（一般来自 CrawlEngine === 'browser'） */
  requireRender?: boolean
}

interface RegionInfo {
  region: string
  workerCount: number
  onlineCount: number
  avgScore: number
}

const RegionWorkerSelector: React.FC<RegionWorkerSelectorProps> = ({
  value = {},
  onChange,
  disabled = false
}) => {
  const [loading, setLoading] = useState(false)
  const [regions, setRegions] = useState<RegionInfo[]>([])

  // 加载 Worker 列表并统计区域信息
  useEffect(() => {
    const loadWorkers = async () => {
      setLoading(true)
      try {
        const workerList = await workerService.getAllWorkers()

        // 统计区域信息
        const regionMap = new Map<string, RegionInfo>()
        
        workerList.forEach((worker) => {
          const region = worker.region || '默认区域'
          
          if (!regionMap.has(region)) {
            regionMap.set(region, {
              region,
              workerCount: 0,
              onlineCount: 0,
              avgScore: 0
            })
          }
          
          const info = regionMap.get(region)!
          info.workerCount++
          
          if (worker.status === 'online') {
            info.onlineCount++

            // 计算负载分数（越低越好）
            const metrics = worker.metrics as { cpu?: number; memory?: number } | undefined
            if (metrics) {
              const cpu = metrics.cpu || 0
              const memory = metrics.memory || 0
              const score = (cpu + memory) / 2
              info.avgScore = (info.avgScore * (info.onlineCount - 1) + score) / info.onlineCount
            }
          }
        })

        setRegions(Array.from(regionMap.values()).sort((a, b) => {
          // 优先按在线 Worker 数排序，其次按负载分数
          if (b.onlineCount !== a.onlineCount) {
            return b.onlineCount - a.onlineCount
          }
          return a.avgScore - b.avgScore
        }))
      } catch (error) {
        Logger.error('加载 Worker 列表失败:', error)
      } finally {
        setLoading(false)
      }
    }
    loadWorkers()
  }, [])

  const handleRegionChange = (region: string | undefined) => {
    onChange?.({
      ...value,
      region: region || undefined
    })
  }

  // 获取当前选中区域的信息
  const selectedRegion = regions.find(r => r.region === value.region)
  const hasAvailableWorkers = regions.some(r => r.onlineCount > 0)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* 区域选择 */}
      <Form.Item
        label={
          <Space>
            <GlobalOutlined />
            执行区域
            <Tooltip title="选择任务执行的区域，系统会自动选择该区域内负载最低的 Worker">
              <QuestionCircleOutlined style={{ color: '#999' }} />
            </Tooltip>
          </Space>
        }
        style={{ marginBottom: 0 }}
      >
        <Select
          value={value.region}
          onChange={handleRegionChange}
          disabled={disabled}
          loading={loading}
          placeholder="自动选择最优区域"
          allowClear
          style={{ width: '100%' }}
          notFoundContent={loading ? <Spin size="small" /> : '暂无可用区域'}
        >
          <Select.Option value="">
            <Space>
              <ThunderboltOutlined style={{ color: '#52c41a' }} />
              <span>自动选择</span>
              <Text type="secondary" style={{ fontSize: 12 }}>
                系统自动选择负载最低的 Worker
              </Text>
            </Space>
          </Select.Option>
          
          {regions.map(region => {
            const available = region.onlineCount
            const isDisabled = available === 0
            
            return (
              <Select.Option 
                key={region.region} 
                value={region.region}
                disabled={isDisabled}
              >
                <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                  <Space>
                    <CloudServerOutlined />
                    <span>{region.region}</span>
                  </Space>
                  <Space>
                    <Tag color={available > 0 ? 'green' : 'default'}>
                      {available} 可用
                    </Tag>
                    {region.avgScore > 0 && (
                      <Text type="secondary" style={{ fontSize: 11 }}>
                        负载: {region.avgScore.toFixed(0)}%
                      </Text>
                    )}
                  </Space>
                </Space>
              </Select.Option>
            )
          })}
        </Select>
      </Form.Item>

      {/* 区域信息提示 */}
      {selectedRegion && (
        <Alert
          type="info"
          showIcon={false}
          message={
            <Space>
              <CloudServerOutlined />
              <Text>
                {selectedRegion.region}: {selectedRegion.onlineCount} 个在线 Worker
                {selectedRegion.avgScore > 0 && `, 平均负载 ${selectedRegion.avgScore.toFixed(0)}%`}
              </Text>
            </Space>
          }
          style={{ padding: '8px 12px' }}
        />
      )}

      {/* 无可用 Worker 警告 */}
      {!loading && !hasAvailableWorkers && (
        <Alert
          type="warning"
          showIcon
          message="当前没有在线 Worker，任务可能无法执行"
        />
      )}
    </div>
  )
}

export default RegionWorkerSelector
