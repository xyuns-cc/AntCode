import { fireEvent, render, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import RegionWorkerSelector from './RegionWorkerSelector'
import type { Worker, WorkerMetrics } from '@/types/worker'

const { getAllWorkers } = vi.hoisted(() => ({ getAllWorkers: vi.fn() }))

vi.mock('@/services/workers', () => ({
  workerService: { getAllWorkers },
}))

const metricsOf = (cpu: number, memory: number): WorkerMetrics => ({
  cpu,
  memory,
  disk: 0,
  taskCount: 0,
  runningTasks: 0,
  projectCount: 0,
  envCount: 0,
  uptime: 0,
})

const workerOf = (name: string, region: string, metrics: WorkerMetrics | null): Worker => ({
  id: name,
  name,
  host: '127.0.0.1',
  port: 8001,
  status: 'online',
  region,
  metrics,
  createdAt: '2026-08-25T00:00:00Z',
})

// metrics === null 是真实回包形态：worker_snapshot_readback 读不回 metrics 列时把该列置空
// 并挂 snapshotErrors（新版 Worker 多报一个键就会走到这里），不是构造出来的假数据。
//
// 三个区域在线台数全是 2，于是排序只剩"负载"这一维，把旧式自算分的方向暴露干净：
// m-region 两台都读不回指标，旧式滑动平均从未被更新过、停在 0，排第一；
// z-region 一台读不回、一台 90% 满载，(0*1+90)/2 = 45，排第二；
// a-region 两台都是老老实实的 50%，反而垫底。
// 也就是"我们对它一无所知"被算成了"它最闲"——与后端 calculate_load_score 的
// "取不到指标返回满分 100（最差）"完全相反。
const WORKERS: Worker[] = [
  workerOf('m-blind-1', 'm-region', null),
  workerOf('m-blind-2', 'm-region', null),
  workerOf('z-blind', 'z-region', null),
  workerOf('z-busy', 'z-region', metricsOf(90, 90)),
  workerOf('a-even-1', 'a-region', metricsOf(50, 50)),
  workerOf('a-even-2', 'a-region', metricsOf(50, 50)),
]

const AUTO_OPTION_TEXT = '自动选择系统自动选择负载最低的 Worker'

const openRegionDropdown = async (container: HTMLElement): Promise<string[]> => {
  await waitFor(() => expect(getAllWorkers).toHaveBeenCalled())
  fireEvent.mouseDown(container.querySelector('.ant-select-selector')!)
  await waitFor(() => {
    expect(document.querySelectorAll('.ant-select-item-option').length).toBe(4)
  })
  return [...document.querySelectorAll('.ant-select-item-option')].map(
    (option) => option.textContent ?? ''
  )
}

describe('区域下拉不自算负载', () => {
  // 证伪项：整份清单做全等比对，而不是 not.toContain('负载')——"自动选择"那条的说明文字
  // 里本来就有"负载最低"四个字，单点包含断言要么恒红要么被迫放宽到失去判别力。
  // 全等同时钉住两件事：选项文案里没有任何自算的百分比，且并列时按区域名排序。
  it('选项文案不含自算百分比，在线台数并列时按区域名排序', async () => {
    getAllWorkers.mockResolvedValue(WORKERS)

    const { container } = render(<RegionWorkerSelector />)

    expect(await openRegionDropdown(container)).toEqual([
      AUTO_OPTION_TEXT,
      'a-region2 在线',
      'm-region2 在线',
      'z-region2 在线',
    ])
  })

  // 证伪项：选中区域后的提示条是第二个渲染点，与下拉选项各自独立。
  it('选中区域的提示条只报在线台数', async () => {
    getAllWorkers.mockResolvedValue(WORKERS)

    const { container } = render(<RegionWorkerSelector value={{ region: 'z-region' }} />)

    await waitFor(() => expect(container.querySelector('.ant-alert-message')).not.toBeNull())
    expect(container.querySelector('.ant-alert-message')!.textContent).toBe(
      'z-region: 2 个在线 Worker'
    )
  })
})
