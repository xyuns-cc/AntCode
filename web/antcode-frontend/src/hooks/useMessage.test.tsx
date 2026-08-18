import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
// 本文件是「静态 API 在 React 19 下失效」的对照组，必须真的调用被禁用的静态方法，
// 否则就成了对着自己的封装自证。生产代码里这两条 lint 规则一律不许豁免。
// eslint-disable-next-line no-restricted-imports
import { App as AntApp, ConfigProvider, Modal, message } from 'antd'
import { describe, expect, it } from 'vitest'

import { globalMessage, globalModal, setMessageInstances } from './useMessage'

/**
 * React 19 + antd v5 的静态 API 全线失效（第五棒 P1）。
 *
 * antd 的静态方法（`import { Modal, message } from 'antd'` 后直接 `Modal.confirm()` /
 * `message.success()`）内部走 React 18 的 `ReactDOM.render`，该 API 在 React 19 已被
 * 移除。后果：三个页面的「批量删除」点了毫无反应（无弹窗、无请求、无报错），大量操作
 * 成功了却零提示——全部不进 console、不发请求，浏览器侧完全不可见。
 *
 * 这组用例带对照组：同一次渲染里，静态调用什么都不出，实例调用（`App.useApp()` 注入
 * 到 `useMessage` 的那套）真的把节点渲染出来。对照组一旦哪天变成「静态也能用」，说明
 * 依赖被换过，这里会红，提醒重新评估迁移结论。
 */

const Harness = () => {
  const instances = AntApp.useApp()
  setMessageInstances(instances.message, instances.notification, instances.modal)
  return (
    <>
      <button onClick={() => globalModal.confirm({ title: '实例确认框', content: '来自 App.useApp()' })}>
        instance-confirm
      </button>
      <button onClick={() => globalMessage.success('实例提示')}>instance-message</button>
      {/* eslint-disable-next-line no-restricted-syntax */}
      <button onClick={() => Modal.confirm({ title: '静态确认框', content: '来自静态 API' })}>static-confirm</button>
      <button onClick={() => message.success('静态提示')}>static-message</button>
    </>
  )
}

const renderHarness = () =>
  render(
    <ConfigProvider>
      <AntApp>
        <Harness />
      </AntApp>
    </ConfigProvider>
  )

describe('antd imperative APIs under React 19', () => {
  it('renders a real confirm dialog through the App.useApp() instance', async () => {
    renderHarness()

    await userEvent.click(screen.getByText('instance-confirm'))

    await waitFor(() => expect(document.querySelector('.ant-modal-confirm')).not.toBeNull())
    expect(document.querySelector('.ant-modal-confirm-title')?.textContent).toBe('实例确认框')
    expect(document.querySelector('.ant-modal-confirm-content')?.textContent).toBe('来自 App.useApp()')
  })

  it('renders a real message through the App.useApp() instance', async () => {
    renderHarness()

    await userEvent.click(screen.getByText('instance-message'))

    await waitFor(() => expect(document.querySelector('.ant-message-notice')).not.toBeNull())
    expect(document.querySelector('.ant-message-notice')?.textContent).toContain('实例提示')
  })

  it('renders nothing at all for the static Modal.confirm', async () => {
    renderHarness()

    await userEvent.click(screen.getByText('static-confirm'))

    // 这就是线上「批量删除是死按钮」的全部真相：调用返回了，DOM 里什么都没有。
    await waitFor(() => expect(document.querySelector('.ant-modal-confirm')).toBeNull())
    expect(screen.queryByText('静态确认框')).toBeNull()
  })

  it('renders nothing at all for the static message.success', async () => {
    renderHarness()

    await userEvent.click(screen.getByText('static-message'))

    await waitFor(() => expect(document.querySelector('.ant-message-notice')).toBeNull())
    expect(screen.queryByText('静态提示')).toBeNull()
  })
})
