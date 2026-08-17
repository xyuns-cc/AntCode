import { act, renderHook } from '@testing-library/react'
import { Modal } from 'antd'
import { describe, expect, it, vi } from 'vitest'

import { useProjectCreateClose, useProjectCreateComplete } from './useProjectCreateDrawer'

/**
 * 创建成功后的收尾路径不能复用「用户主动关闭」的放弃确认。
 *
 * 提交成功时表单必然是脏的，`useProjectCreateClose` 会弹「放弃创建？」并把复位挂在
 * 用户点确认上；用户不点（或抽屉被父组件先关掉）就永远不复位，再次打开会停在第 2 步
 * 并保留上一次的全部输入。
 */
describe('project create drawer completion', () => {
  it('resets and closes without asking to discard after a successful create', () => {
    const confirm = vi.spyOn(Modal, 'confirm')
    const reset = vi.fn()
    const onClose = vi.fn()

    const { result } = renderHook(() => useProjectCreateComplete(reset, onClose))
    act(() => result.current())

    expect(reset).toHaveBeenCalledTimes(1)
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(confirm).not.toHaveBeenCalled()
  })

  it('still asks to discard when the user closes a dirty form by hand', () => {
    const confirm = vi.spyOn(Modal, 'confirm').mockReturnValue({
      destroy: vi.fn(),
      update: vi.fn(),
    } as unknown as ReturnType<typeof Modal.confirm>)
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
