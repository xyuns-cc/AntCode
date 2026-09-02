/**
 * 监控页那张表原来读 `specified_worker_id` 再去 Worker 列表反查名字，于是 auto / prefer
 * 策略的任务（这两种本来就没有任务级指定）在**运行中**也被写成「未分配」——把"不需要
 * 绑定"说成了"没人接"。列名还叫「执行 Worker」，把配置值冒充成生效值。
 *
 * 后端任务列表没有「实际执行在哪台」（见 describeTaskWorkerBinding 的注释），所以这里
 * 只能如实说绑定；判据成对：正例钉每种策略该显示什么，反例钉「任何策略都不会再出现
 * 『未分配』」。
 */
import { describe, expect, it } from 'vitest'
import { describeTaskWorkerBinding } from './taskWorkerBinding'

describe('describeTaskWorkerBinding', () => {
  it('auto 策略说「自动选择」，不是「未分配」', () => {
    const label = describeTaskWorkerBinding({ execution_strategy: 'auto', specified_worker_id: null })

    expect(label).toBe('自动选择')
    expect(label).not.toBe('未分配')
  })

  it('specified 策略取后端给的 Worker 名，缺名字才退回 id', () => {
    expect(describeTaskWorkerBinding({
      execution_strategy: 'specified',
      specified_worker_id: 'w-1',
      specified_worker_name: 'node-01',
    })).toBe('node-01')

    expect(describeTaskWorkerBinding({
      execution_strategy: 'specified',
      specified_worker_id: 'w-1',
    })).toBe('w-1')
  })

  it('fixed / prefer 走项目绑定，任务级 specified 字段为空不算「未分配」', () => {
    for (const strategy of ['fixed', 'prefer'] as const) {
      const label = describeTaskWorkerBinding({
        execution_strategy: strategy,
        specified_worker_id: null,
        project_bound_worker_name: 'node-07',
      })

      expect(label).toBe('node-07')
    }
  })

  it('任何输入都不会再产出「未分配」', () => {
    const inputs = [
      {},
      { execution_strategy: 'auto' as const },
      { execution_strategy: 'specified' as const },
      { execution_strategy: 'fixed' as const },
      { execution_strategy: 'prefer' as const },
      { project_execution_strategy: 'auto' as const },
    ]

    for (const input of inputs) {
      expect(describeTaskWorkerBinding(input)).not.toBe('未分配')
    }
  })
})
