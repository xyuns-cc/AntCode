import { describe, expect, it } from 'vitest'

import { buildTaskCreateRequest, type TaskCreateFormValues } from './taskCreateRequest'

const baseValues = {
  name: 'demo',
  project_id: 'project-1',
  schedule_type: 'once',
} as unknown as TaskCreateFormValues

describe('buildTaskCreateRequest', () => {
  it('缺省时填入调度/重试默认值', () => {
    const request = buildTaskCreateRequest(baseValues)
    expect(request).toMatchObject({
      max_instances: 1,
      timeout_seconds: 3600,
      retry_count: 3,
      retry_delay: 60,
      is_active: true,
    })
  })

  it('只有 execution_strategy 为 specified 时才带上指定 Worker', () => {
    const specified = buildTaskCreateRequest({
      ...baseValues,
      execution_strategy: 'specified',
      specified_worker_id: 'worker-1',
    } as TaskCreateFormValues)
    expect(specified.specified_worker_id).toBe('worker-1')

    const auto = buildTaskCreateRequest({
      ...baseValues,
      execution_strategy: 'auto',
      specified_worker_id: 'worker-1',
    } as TaskCreateFormValues)
    expect(auto.specified_worker_id).toBeUndefined()
  })

  it('把 scheduled_time 控件对象转成 ISO 字符串', () => {
    const request = buildTaskCreateRequest({
      ...baseValues,
      scheduled_time: { toISOString: () => '2026-08-17T01:02:03.000Z' },
    })
    expect(request.scheduled_time).toBe('2026-08-17T01:02:03.000Z')
  })

  it('解析 JSON 文本字段，未填写时为 undefined', () => {
    const request = buildTaskCreateRequest({
      ...baseValues,
      execution_params: '{"depth":2}',
    })
    expect(request.execution_params).toEqual({ depth: 2 })
    expect(request.environment_vars).toBeUndefined()
  })

  it('is_active 显式为 false 时保持关闭', () => {
    expect(buildTaskCreateRequest({ ...baseValues, is_active: false }).is_active).toBe(false)
  })
})
