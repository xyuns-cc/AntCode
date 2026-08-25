import type React from 'react'
import { useEffect, useState } from 'react'
import { Form, Select, Space, Switch, Tag, Tooltip, Alert, Typography, Spin } from 'antd'
import {
  GlobalOutlined,
  CloudServerOutlined,
  QuestionCircleOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'

import { workerService } from '@/services/workers'
import type { Worker } from '@/types/worker'
import Logger from '@/utils/logger'

const { Text } = Typography

interface RegionWorkerSelectorProps {
  value?: {
    region?: string
    require_render?: boolean
  }
  onChange?: (value: { region?: string; require_render?: boolean }) => void
  disabled?: boolean
  /** 是否需要浏览器渲染能力，会影响可选 worker（一般来自 CrawlEngine === 'browser'） */
  requireRender?: boolean
}

// 只放前端自己能从回包直接读出的事实（区域名 + status === 'online' 的台数）。
//
// 这里曾经还有一个 avgScore = (cpu + memory) / 2 的区域平均负载，删掉的理由有两层：
//
// 1. 负载分的唯一真源在后端 calculate_load_score（CPU / 内存 / 并发槽位三项均分，
//    取不到指标返回满分 100 —— 不可知按最坏算）。前端复刻一份就是同一个概念的第二个
//    可分叉真源，而且已经分叉过：后端 9eb0963 把判据从五项收到三项时，这里这份两项的
//    抄本一动没动。它还把方向抄反了 —— 跳过无指标的机器、却照样按在线台数取平均，
//    等于把"我们对它一无所知"算成"它最闲"，把一无所知的区域顶到列表第一位。
// 2. 就算公式抄对了，按区域取平均也预测不了任何东西：派发取的是区域内分数**最低**且
//    通过 is_worker_available 的那一台（select_best_worker），不是区域的平均水平。
//
// 后端的分只经 /api/v1/workers/load/ranking 与 /best 暴露，两条都挂
// require_role(ADMIN, SUPER_ADMIN)；而本组件渲染在项目创建/编辑抽屉里，创建项目只要求
// 登录。要在这里显示真分数就得放宽那两条的鉴权，所以选择不显示，而不是显示一个假的。
interface RegionInfo {
  region: string
  onlineCount: number
}

// 在线台数相同时按区域名排序：这是个纯粹的定序手段，不宣称任何一个区域更优。
// 之前这里排的是自算的平均负载，于是"排在前面"看起来像一条推荐，而它推荐的恰恰是
// 指标读不回来的那个区域。
const summarizeRegions = (workerList: readonly Worker[]): RegionInfo[] => {
  const regionMap = new Map<string, RegionInfo>()

  workerList.forEach((worker) => {
    const region = worker.region?.trim()
    if (!region) return

    const info = regionMap.get(region) ?? { region, onlineCount: 0 }
    regionMap.set(region, {
      ...info,
      onlineCount: info.onlineCount + (worker.status === 'online' ? 1 : 0),
    })
  })

  return [...regionMap.values()].sort(
    (a, b) => b.onlineCount - a.onlineCount || a.region.localeCompare(b.region)
  )
}

const RegionWorkerSelector: React.FC<RegionWorkerSelectorProps> = ({
  value = {},
  onChange,
  disabled = false,
  requireRender = false,
}) => {
  const [loading, setLoading] = useState(false)
  const [regions, setRegions] = useState<RegionInfo[]>([])
  const [onlineWorkerCount, setOnlineWorkerCount] = useState(0)

  // 加载 Worker 列表并统计区域信息
  useEffect(() => {
    const loadWorkers = async () => {
      setLoading(true)
      try {
        const workerList = await workerService.getAllWorkers()
        setOnlineWorkerCount(workerList.filter((worker) => worker.status === 'online').length)
        setRegions(summarizeRegions(workerList))
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
      region: region || undefined,
    })
  }

  const handleRenderChange = (require_render: boolean) => {
    onChange?.({ ...value, require_render })
  }

  // 获取当前选中区域的信息
  const selectedRegion = regions.find((r) => r.region === value.region)
  const hasOnlineWorkers = onlineWorkerCount > 0

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

          {regions.map((region) => {
            const online = region.onlineCount

            return (
              <Select.Option key={region.region} value={region.region} disabled={online === 0}>
                <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                  <Space>
                    <CloudServerOutlined />
                    <span>{region.region}</span>
                  </Space>
                  {/* 报"在线"而不是"可用"：在线只需 status，可用还要过后端 is_worker_available
                      的 CPU / 内存阈值与并发槽位，前端算不出，不能替它下结论。 */}
                  <Tag color={online > 0 ? 'green' : 'default'}>{online} 在线</Tag>
                </Space>
              </Select.Option>
            )
          })}
        </Select>
      </Form.Item>

      <Form.Item label="浏览器渲染能力" style={{ marginBottom: 0 }}>
        <Switch
          checked={requireRender || Boolean(value.require_render)}
          disabled={disabled || requireRender}
          onChange={handleRenderChange}
        />
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
              </Text>
            </Space>
          }
          style={{ padding: '8px 12px' }}
        />
      )}

      {/* 无可用 Worker 警告 */}
      {!loading && !hasOnlineWorkers && (
        <Alert type="warning" showIcon message="当前没有在线 Worker，任务可能无法执行" />
      )}
    </div>
  )
}

export default RegionWorkerSelector
