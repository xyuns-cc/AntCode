import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { installFakeAntdInstances } from '@/test/antdInstances'
import { useProjectCreateClose, useProjectCreateComplete } from './useProjectCreateDrawer'

/**
 * 创建成功后的收尾路径不能复用「用户主动关闭」的放弃确认。
 *
 * 提交成功时表单必然是脏的，`useProjectCreateClose` 会弹「放弃创建？」并把复位挂在
 * 用户点确认上；用户不点（或抽屉被父组件先关掉）就永远不复位，再次打开会停在第 2 步
 * 并保留上一次的全部输入。
 *
 * 断言打在 `App.useApp()` 注入的 modal 实例上，而不是 antd 的静态 `Modal.confirm`：
 * 静态方法在 React 19 下是空操作，对着它断言会一直绿而线上按钮根本不弹窗。
 */
describe('project create drawer completion', () => {
  let instances: ReturnType<typeof installFakeAntdInstances>

  beforeEach(() => {
    instances = installFakeAntdInstances()
  })

  it('resets and closes without asking to discard after a successful create', () => {
    const reset = vi.fn()
    const onClose = vi.fn()

    const { result } = renderHook(() => useProjectCreateComplete(reset, onClose))
    act(() => result.current())

    expect(reset).toHaveBeenCalledTimes(1)
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(instances.modal.confirm).not.toHaveBeenCalled()
  })

  it('still asks to discard when the user closes a dirty form by hand', () => {
    const confirm = instances.modal.confirm
    const reset = vi.fn()
    const onClose = vi.fn()

    const { result } = renderHook(() =>
      useProjectCreateClose({
        loading: false,
        projectType: 'code',
        envConfig: null,
        regionConfig: {},
        formData: { name: 'half typed' },
        reset,
        onClose,
      })
    )
    act(() => result.current())

    expect(confirm).toHaveBeenCalledTimes(1)
    expect(reset).not.toHaveBeenCalled()
    expect(onClose).not.toHaveBeenCalled()
  })
})
