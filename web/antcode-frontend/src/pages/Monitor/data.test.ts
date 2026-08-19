import { describe, expect, it } from 'vitest'

import * as monitorData from './data'
import { createAlerts } from './data'
import type { WorkerDisplayData } from './types'

describe('Monitor data contracts', () => {
  it('does not present derived Worker state as authoritative logs', () => {
    expect(monitorData).not.toHaveProperty('createWorkerLogs')
  })
})

const worker = (overrides: Partial<WorkerDisplayData>): WorkerDisplayData => ({
  id: 'w-1',
  name: 'worker-1',
  version: 'v1.0.0',
  os: 'linux',
  status: 'running',
  cpu: 0,
  memory: 0,
  disk: 0,
  tasks: 0,
  uptime: '1小时 0分钟',
  host: '127.0.0.1',
  port: 8000,
  ...overrides,
})

// 回归：Worker 上报的使用率是 float32 转来的 double，85.9 会变成
// 85.9000015258789，此前直接插进模板字符串，告警卡片上原样展示。
describe('createAlerts renders usage percentages as readable numbers', () => {
  it.each([
    ['disk', { disk: 85.9000015258789 }, '磁盘使用率85.9%'],
    ['cpu', { cpu: 91.30000305175781 }, 'CPU使用率超过85%，当前91.3%'],
    ['memory-warning', { memory: 72.19999694824219 }, '内存使用率72.2%，建议关注'],
  ])('formats %s usage', (_name, metrics, expected) => {
    const [alert] = createAlerts([worker(metrics)], '12:00:00')

    expect(alert.message).toContain(expected)
    expect(alert.message).not.toMatch(/\d\.\d{3,}%/)
  })

  it('keeps an integral usage free of a trailing .0', () => {
    const [alert] = createAlerts([worker({ disk: 90 })], '12:00:00')

    expect(alert.message).toContain('磁盘使用率90%')
  })
})
