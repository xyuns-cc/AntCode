/**
 * 资源页展示的必须是**生效值**，且"不知道"要显示成占位符。
 *
 * 走真实组件渲染 + 真实 service 往返（只 spy 掉 HTTP 那一层），不直调内部 handler：
 * 缺陷本身长在 `limits` 到 antd `Statistic` 的这段渲染上，绕过渲染就测不到。
 *
 * antd `Statistic` 对 `undefined` 走默认参数渲染成 `0`、对 `null` 渲染成字面量
 * `null`——两种都会被当成真实限额读走，所以这里断言的是"页面上不出现 0/null"，
 * 而不只是"工具函数返回了占位符"。
 */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { WorkerResourceInfo } from '@/types'
import { workerService } from '@/services/workers'
import { useAuthStore } from '@/stores/authStore'
import showNotification from '@/utils/notification'
import WorkerResourceManagement from './WorkerResourceManagement'

vi.mock('@/utils/notification', () => ({ default: vi.fn() }))
const notify = vi.mocked(showNotification)

// 真机 mn-worker-02 的形状：从没被 API 设过限额，心跳只带得动并发。
const EFFECTIVE_CONCURRENCY = 4

const STATS: WorkerResourceInfo['resource_stats'] = {
  cpu_percent: 1,
  memory_percent: 2,
  disk_percent: 3,
  memory_used_mb: 100,
  memory_total_mb: 3072,
  disk_used_gb: 1,
  disk_total_gb: 10,
  running_tasks: 0,
  queued_tasks: 0,
  uptime_seconds: 60
}

function resources(overrides: Partial<WorkerResourceInfo>): WorkerResourceInfo {
  return {
    limits: {
      max_concurrent_tasks: EFFECTIVE_CONCURRENCY,
      task_memory_limit_mb: null,
      task_cpu_time_limit_sec: null
    },
    configured_limits: {
      max_concurrent_tasks: null,
      task_memory_limit_mb: null,
      task_cpu_time_limit_sec: null
    },
    auto_adjustment: true,
    resource_stats: STATS,
    ...overrides
  }
}

const SUPER_ADMIN = {
  id: 'u2',
  username: 'root',
  is_active: true,
  is_admin: true,
  role: 'super_admin' as const,
  created_at: '',
  updated_at: ''
}

// 表单三项都是 required，保存路径的用例必须先有一份填得满的配置。
function fullyConfigured(): WorkerResourceInfo {
  return resources({
    configured_limits: {
      max_concurrent_tasks: EFFECTIVE_CONCURRENCY,
      task_memory_limit_mb: 512,
      task_cpu_time_limit_sec: 480
    }
  })
}

function renderWith(data: WorkerResourceInfo) {
  vi.spyOn(workerService, 'getWorkerResources').mockResolvedValue(data)
  return render(<WorkerResourceManagement workerId="worker-1" workerName="mn-worker-02" />)
}

describe('WorkerResourceManagement 生效限额展示', () => {
  beforeEach(() => {
    useAuthStore.setState({
      user: {
        id: 'u1',
        username: 'ops',
        is_active: true,
        is_admin: true,
        role: 'admin',
        created_at: '',
        updated_at: ''
      },
      isAuthenticated: true
    })
  })

  it('Worker 没上报的限额显示占位符，而不是 0 或字面量 null', async () => {
    const { container } = renderWith(resources({}))

    await waitFor(() => expect(screen.getByText('最大并发（生效）')).toBeInTheDocument())
    expect(container.textContent).toContain('—')
    expect(container.textContent).not.toContain('null')
    // 生效并发是心跳真值，必须照原样出现。
    expect(screen.getByText('最大并发（生效）').parentElement?.textContent).toContain(String(EFFECTIVE_CONCURRENCY))
  })

  it('不再把 web-api settings 的 10/1024/600 当作生效值渲染', async () => {
    const { container } = renderWith(resources({}))

    await waitFor(() => expect(screen.getByText('最大并发（生效）')).toBeInTheDocument())
    // 这三个是 web-api settings 的默认值，真机上 Worker 实际跑 4/537/480。
    expect(container.textContent).not.toContain('1024')
    expect(container.textContent).not.toContain('600')
  })

  it('下发值与生效值分叉时明确告警，而不是只显示其中一个', async () => {
    const { container } = renderWith(
      resources({
        limits: {
          max_concurrent_tasks: EFFECTIVE_CONCURRENCY,
          task_memory_limit_mb: null,
          task_cpu_time_limit_sec: null
        },
        configured_limits: {
          max_concurrent_tasks: 20,
          task_memory_limit_mb: 2808,
          task_cpu_time_limit_sec: null
        }
      })
    )

    await waitFor(() => expect(screen.getByText('配置值未生效')).toBeInTheDocument())
    expect(container.textContent).toContain('最大并发')
    // 两边都有真值才算分叉；内存侧生效值未知，不能拿它当"不一致"报出去。
    expect(screen.getByText(/的下发值与 Worker 上报的生效值不一致/).textContent).not.toContain('内存限制')
  })

  it('保存成功的提示不许声称"已生效"——Worker 才是权威', async () => {
    useAuthStore.setState({ user: SUPER_ADMIN, isAuthenticated: true })
    renderWith(fullyConfigured())
    vi.spyOn(workerService, 'updateWorkerResources').mockResolvedValue({ updated: {}, synced: true })

    await waitFor(() => expect(screen.getByRole('button', { name: /保存配置/ })).toBeEnabled())
    await userEvent.click(screen.getByRole('button', { name: /保存配置/ }))

    await waitFor(() => expect(notify).toHaveBeenCalled())
    const [type, message] = notify.mock.calls.at(-1)!
    expect(type).toBe('success')
    expect(String(message)).toContain('已下发')
    expect(String(message)).not.toContain('已更新')
  })

  it('控制事件没能下发时报警告而不是成功', async () => {
    useAuthStore.setState({ user: SUPER_ADMIN, isAuthenticated: true })
    renderWith(fullyConfigured())
    vi.spyOn(workerService, 'updateWorkerResources').mockResolvedValue({ updated: {}, synced: false })

    await waitFor(() => expect(screen.getByRole('button', { name: /保存配置/ })).toBeEnabled())
    await userEvent.click(screen.getByRole('button', { name: /保存配置/ }))

    await waitFor(() => expect(notify).toHaveBeenCalled())
    const [type, message] = notify.mock.calls.at(-1)!
    expect(type).toBe('warning')
    expect(String(message)).toContain('未能下发')
  })

  it('生效值与下发值一致时不误报分叉', async () => {
    renderWith(
      resources({
        configured_limits: {
          max_concurrent_tasks: EFFECTIVE_CONCURRENCY,
          task_memory_limit_mb: null,
          task_cpu_time_limit_sec: null
        }
      })
    )

    await waitFor(() => expect(screen.getByText('最大并发（生效）')).toBeInTheDocument())
    expect(screen.queryByText('配置值未生效')).not.toBeInTheDocument()
  })
})
