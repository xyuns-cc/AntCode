import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { describeBatchFailures } from './batchOutcome'

const apiMocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
}))

vi.mock('./api', () => ({ default: apiMocks, unwrapResponse: (response: unknown) => response }))

const { taskService } = await import('./tasks')
const { projectService } = await import('./projects')

// 后端守卫的原话：点名仍在线的 Worker。它是运维唯一能据此行动的线索，
// 任何一层把它换成"某几个失败了"都算回归。
const HOLDER_REASON = '执行 run-9 仍由在线 Worker worker-a 持有，请等待其上报结算结果后再删除'
const IN_FLIGHT_REASON = '项目存在未终态执行，请先取消并等待执行结束'

function envelope(data: unknown) {
  return { data: { success: true, code: 200, message: 'ok', data } }
}

function pageSource(relativePath: string): string {
  return readFileSync(fileURLToPath(new URL(relativePath, import.meta.url)), 'utf-8')
}

// 键名的唯一真源，与 tests/unit/web_api/test_batch_failure_reasons.py 读同一份文件：
// 后端改名而前端没跟，必有一侧变红。
interface FailureContract {
  failure_item_keys: string[]
  endpoints: { name: string; legacy_failed_ids_key: string; data_keys: string[] }[]
}

const CONTRACT: FailureContract = JSON.parse(pageSource('../../../../contracts/http/batch_delete_failures.json'))

function contractFor(name: string) {
  const endpoint = CONTRACT.endpoints.find((candidate) => candidate.name === name)
  if (!endpoint) throw new Error(`契约缺少端点: ${name}`)
  return endpoint
}

beforeEach(() => {
  apiMocks.post.mockReset()
})

describe('describeBatchFailures', () => {
  it('把每个 id 配上原话，不做二次改写', () => {
    const text = describeBatchFailures([
      { id: 'task-1', reason: HOLDER_REASON },
      { id: 'task-2', reason: IN_FLIGHT_REASON },
    ])

    expect(text).toBe(`task-1：${HOLDER_REASON}；task-2：${IN_FLIGHT_REASON}`)
  })

  it('没有失败项时是空串', () => {
    expect(describeBatchFailures([])).toBe('')
  })
})

describe('批量删除响应里的逐项原因', () => {
  it('taskService 透传 failures，并保留旧的 failed_ids 字段', async () => {
    apiMocks.post.mockResolvedValue(
      envelope({
        success_count: 1,
        failed_count: 1,
        failed_ids: ['task-2'],
        failures: [{ id: 'task-2', reason: HOLDER_REASON }],
      })
    )

    const result = await taskService.batchDeleteTasks(['task-1', 'task-2'])

    expect(result.failed_ids).toEqual(['task-2'])
    expect(result.failures).toEqual([{ id: 'task-2', reason: HOLDER_REASON }])
    expect(describeBatchFailures(result.failures)).toContain(HOLDER_REASON)
  })

  it('projectService 透传 failures，并保留旧的 failed_projects 字段', async () => {
    apiMocks.post.mockResolvedValue(
      envelope({
        total: 2,
        success_count: 1,
        failed_count: 1,
        failed_projects: ['project-2'],
        failures: [{ id: 'project-2', reason: IN_FLIGHT_REASON }],
      })
    )

    const result = await projectService.batchDeleteProjects(['project-1', 'project-2'])

    expect(result.failed_projects).toEqual(['project-2'])
    expect(result.failures).toEqual([{ id: 'project-2', reason: IN_FLIGHT_REASON }])
    expect(describeBatchFailures(result.failures)).toContain(IN_FLIGHT_REASON)
  })
})

describe('响应键名与后端契约同源', () => {
  it.each([
    ['tasks_batch_delete', 'failed_ids'],
    ['tasks_batch_operate', 'failed_ids'],
    ['projects_batch_delete', 'failed_projects'],
  ])('%s 保留旧字段并新增 failures', (name, legacyKey) => {
    const endpoint = contractFor(name)

    expect(endpoint.legacy_failed_ids_key).toBe(legacyKey)
    expect(endpoint.data_keys).toContain(legacyKey)
    expect(endpoint.data_keys).toContain('failures')
  })

  it('failures 元素的键就是 describeBatchFailures 读的那两个', () => {
    const item = Object.fromEntries(
      CONTRACT.failure_item_keys.map((key) => [key, key === 'id' ? 'task-2' : HOLDER_REASON])
    ) as { id: string; reason: string }

    expect(CONTRACT.failure_item_keys).toEqual(['id', 'reason'])
    expect(describeBatchFailures([item])).toBe(`task-2：${HOLDER_REASON}`)
  })
})

// 两个列表页体量太大，整页渲染不现实；但"拿到了 failures 却没展示"正是这次要修的
// 形状，只测 service 透传会留下一个假绿缺口。这里对调用点做结构断言：谁把提示改回
// 只报数量或只报 id，下面两条立刻红。
describe('列表页把逐项原因接进提示文案', () => {
  it.each([
    ['Tasks', '../pages/Tasks/index.tsx'],
    ['Projects', '../pages/Projects/ProjectList.tsx'],
  ])('%s 列表页的批量删除提示引用 result.failures', (_name, relativePath) => {
    expect(pageSource(relativePath)).toContain('describeBatchFailures(result.failures)')
  })
})
