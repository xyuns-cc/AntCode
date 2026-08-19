import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { Form } from 'antd'
import type {
  GitRepository,
  ProjectImportFormValues,
  ProjectImportItem,
  RepositoryScanResult,
} from '@/types/repository'
import { buildImportDefaults, buildImportProjects } from './helpers'
import ScanImportDrawer from './components/ScanImportDrawer'

vi.mock('@/services/workers', () => ({
  workerService: {
    getMyAvailableWorkers: vi.fn().mockResolvedValue([
      { id: 'worker-1', name: 'worker-1', status: 'online', pythonVersion: '3.11' },
    ]),
  },
}))

const repository: GitRepository = {
  id: 'repo-1',
  name: 'crawler',
  url: 'https://example.test/crawler.git',
  default_ref: 'main',
  enabled: true,
  created_at: '2026-07-27T00:00:00Z',
  updated_at: '2026-07-27T00:00:00Z',
}

const scanResult: RepositoryScanResult = {
  repository_id: repository.id,
  ref: 'main',
  candidates: [{ subdir: 'spiders/news', entry_point: 'main.py', markers: ['main.py'] }],
}

describe('repository import helpers', () => {
  it('binds every imported project to the selected runtime Worker', () => {
    const defaults = buildImportDefaults(scanResult, ['spiders/news'])
    const projects = buildImportProjects({
      ...defaults,
      worker_id: 'worker-1',
      python_version: '3.11.9',
    }, ['spiders/news'], scanResult, repository)

    expect(projects[0]).toMatchObject({
      worker_id: 'worker-1',
      bound_worker_id: 'worker-1',
      python_version: '3.11.9',
      runtime_scope: 'private',
      execution_strategy: 'fixed',
    })
  })

  // 回归：后端 ProjectImportItem 把 repository_id / subdir / entry_point 列为
  // 必填（domain/schemas/repository.py:72-75）。历史缺陷是这些字段只存在于表单
  // store 里、没有对应的 Form.Item，antd validateFields() 原样丢弃，
  // 请求被后端 422 挡下（body.projects.0.repository_id: Field required）。
  it('carries every backend-required field', () => {
    const defaults = buildImportDefaults(scanResult, ['spiders/news'])
    const [project] = buildImportProjects({
      ...defaults,
      worker_id: 'worker-1',
      python_version: '3.11',
    }, ['spiders/news'], scanResult, repository)

    const required: (keyof ProjectImportItem)[] = [
      'repository_id', 'ref', 'subdir', 'entry_point',
      'name', 'python_version', 'worker_id', 'bound_worker_id',
    ]
    required.forEach(field => {
      expect(project[field], `缺少后端必填字段 ${field}`).toBeTruthy()
    })
    expect(project).toMatchObject({
      repository_id: 'repo-1',
      ref: 'main',
      subdir: 'spiders/news',
      entry_point: 'main.py',
      name: 'spiders-news',
    })
  })

  it.each([
    [{ worker_id: undefined, python_version: '3.11' }, '必须选择 Worker'],
    [{ worker_id: 'worker-1', python_version: 'latest' }, '有效的 Python 版本'],
  ])('rejects an incomplete runtime selection', (runtime, message) => {
    const defaults = buildImportDefaults(scanResult, ['spiders/news'])

    expect(() => buildImportProjects(
      { ...defaults, ...runtime }, ['spiders/news'], scanResult, repository,
    )).toThrow(message)
  })
})

// 上面几条都是纯函数断言，喂进去的 values 由测试自己拼装，**无法证明 antd
// 表单真的会把这些值交回来** —— 历史假绿正出在这里。下面这条把真实的
// ScanImportDrawer 挂载起来，完整走一遍 setFieldsValue → validateFields()，
// 再用表单**实际回传**的值构造请求体。
describe('scan import drawer form round-trip', () => {
  const renderDrawer = () => {
    const captured: { form?: ReturnType<typeof Form.useForm>[0] } = {}
    const Harness = () => {
      const [form] = Form.useForm()
      captured.form = form
      return (
        <ScanImportDrawer
          open
          repository={repository}
          scanResult={scanResult}
          selectedSubdirs={['spiders/news']}
          form={form}
          scanRef="main"
          onScanRefChange={() => {}}
          onClose={() => {}}
          onScan={() => {}}
          onImport={() => {}}
          onSelectionChange={() => {}}
        />
      )
    }
    render(<Harness />)
    return captured
  }

  it('produces a payload carrying every backend-required field', async () => {
    const captured = renderDrawer()
    await waitFor(() => expect(screen.getByText('运行 Worker')).toBeTruthy())
    await waitFor(() => expect(screen.getByPlaceholderText('例如 3.11')).toBeTruthy())

    const form = captured.form!
    form.setFieldsValue({
      ...buildImportDefaults(scanResult, ['spiders/news']),
      worker_id: 'worker-1',
      python_version: '3.11',
    })

    // 刻意不做类型放宽以外的加工：这里就是要断言"antd 实际回传了什么"。
    const values = (await form.validateFields()) as ProjectImportFormValues
    const [project] = buildImportProjects(values, ['spiders/news'], scanResult, repository)

    expect(project.repository_id).toBe('repo-1')
    expect(project.subdir).toBe('spiders/news')
    expect(project.entry_point).toBe('main.py')
    expect(project.ref).toBe('main')
    expect(project.name).toBe('spiders-news')
    expect(project.worker_id).toBe('worker-1')
    expect(project.bound_worker_id).toBe('worker-1')
    expect(project.python_version).toBe('3.11')
  })
})
