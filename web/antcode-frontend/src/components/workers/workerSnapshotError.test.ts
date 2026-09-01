/**
 * 两种读回失败的 tooltip 措辞必须跟着 reason 走。
 *
 * 后端把"字段漂移"与"整列不是 JSON 对象"分成了两个 reason，因为处置完全不同：
 * 前者去补读回 schema，后者去查是谁把数组/二次编码的字符串写进了这一列。两种都套
 * "控制面 schema 未声明或取值越界"的话，运维会照着一个并不存在的键去改 schema。
 *
 * 判据只认 reason，不认 message 里的中文——仓里有 `"NOSCRIPT" in str(exc)` 那种
 * 字符串契约的 P0 前科。
 *
 * **证伪方式**：把 snapshotErrorTooltip 里的 reason 分支删掉（恒用旧文案），
 * `not_an_object` 那条变红。
 */
import { describe, expect, it } from 'vitest'
import { snapshotErrorTooltip } from './workerSnapshotError'

const SCHEMA_WORDING = '控制面 schema 未声明或取值越界'
const STRUCTURE_WORDING = '该列不是 JSON 对象'

describe('snapshotErrorTooltip', () => {
  it('结构坏了时不许说成"schema 未声明"，那会把人引去补一个不存在的字段', () => {
    const tooltip = snapshotErrorTooltip({
      column: 'metrics',
      reason: 'not_an_object',
      keys: [],
      message: '该列必须是 JSON 对象，实际存的是 list'
    })

    expect(tooltip).toContain(STRUCTURE_WORDING)
    expect(tooltip).not.toContain(SCHEMA_WORDING)
    expect(tooltip).toContain('list')
  })

  it('字段漂移仍然指向 schema，并带上键名', () => {
    const tooltip = snapshotErrorTooltip({
      column: 'metrics',
      reason: 'field_mismatch',
      keys: ['gpuUtilization'],
      message: 'gpuUtilization: Extra inputs are not permitted'
    })

    expect(tooltip).toContain(SCHEMA_WORDING)
    expect(tooltip).toContain('gpuUtilization')
  })

  it('field_mismatch 用 schema 措辞，不冒充结构故障', () => {
    const tooltip = snapshotErrorTooltip({
      column: 'capabilities',
      reason: 'field_mismatch',
      keys: ['unknownFutureCapability'],
      message: 'unknownFutureCapability: Extra inputs are not permitted'
    })

    expect(tooltip).toContain(SCHEMA_WORDING)
    expect(tooltip).not.toContain(STRUCTURE_WORDING)
  })
})
