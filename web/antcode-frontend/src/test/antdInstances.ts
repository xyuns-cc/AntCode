import { vi } from 'vitest'

import { setMessageInstances } from '@/hooks/useMessage'

/**
 * 把 `App.useApp()` 会注入的三个实例换成可断言的假实例。
 *
 * 生产里这三个实例由 `AppInitializer` 从 `App.useApp()` 取得并注册进
 * `hooks/useMessage`。测试里对着它们断言，等于验证调用点确实走了 context 实例这条
 * 活路；如果哪天有人退回 antd 的静态 `message.*` / `Modal.confirm`（React 19 下是
 * 空操作），断言拿不到调用就会立刻变红。
 */
export const installFakeAntdInstances = () => {
  const message = {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
    loading: vi.fn(),
    open: vi.fn(),
    destroy: vi.fn(),
  }
  const notification = {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
    open: vi.fn(),
    destroy: vi.fn(),
  }
  const modal = {
    confirm: vi.fn(() => ({ destroy: vi.fn(), update: vi.fn() })),
    info: vi.fn(),
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
  }
  setMessageInstances(
    message as unknown as Parameters<typeof setMessageInstances>[0],
    notification as unknown as Parameters<typeof setMessageInstances>[1],
    modal as unknown as Parameters<typeof setMessageInstances>[2]
  )
  return { message, notification, modal }
}
